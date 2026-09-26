# app/integrations/hyperliquid_client.py
"""
Нативный клиент Hyperliquid (api.hyperliquid.xyz/info) — независимая проверка
app-chain позиций, которые Rabby с 11.09.2026 отдаёт с чужими суммами.

Публичный API без авторизации. Лимит ~1200 weight/мин на IP (info-запросы
weight 2), поэтому token-bucket ведётся на каждый исходящий IP (прокси).

Считаем (три запроса идут параллельно):
  * perp   — clearinghouseState.marginSummary.accountValue (маржа + uPnL);
  * spot   — spotClearinghouseState.balances × midPx пары TOKEN/USDC;
  * stake  — delegatorSummary (delegated + undelegated + pending) × цена HYPE.
Любая сетевая/схемная ошибка → исключение (fail-closed): позиция не будет
принята «по умолчанию нулём», выборка повторится.
"""
from __future__ import annotations

import time
from typing import Any

from app.core.parallel import Memo, run_parallel, unwrap
from app.integrations import http_pool
from app.integrations.http_pool import PerKeyLimiter

HL_API_URL = "https://api.hyperliquid.xyz/info"
HL_RATE_PER_SEC = 9.0  # на один IP (weight 2 → 1080/мин < 1200)
APPCHAIN_TIMEOUT = 8

_LIMITER = PerKeyLimiter(HL_RATE_PER_SEC)
_PRICES = Memo(ttl=60)


class HyperliquidError(RuntimeError):
    pass


def _post(payload: dict, proxy: str | None) -> Any:
    attempts, pause = http_pool.retry_policy(proxy)
    last: Exception | None = None
    for attempt in range(attempts):
        _LIMITER.wait(proxy)
        try:
            resp = http_pool.post(HL_API_URL, proxy, json=payload, timeout=APPCHAIN_TIMEOUT)
            if resp.status_code == 429:
                time.sleep(pause if proxy else min(3.0 * (attempt + 1), 30.0))
                last = HyperliquidError("HTTP 429 from Hyperliquid")
                continue
            if resp.status_code != 200:
                raise HyperliquidError(f"HTTP {resp.status_code} from Hyperliquid ({payload.get('type')})")
            return resp.json()
        except (HyperliquidError, http_pool.ProxyDead):
            raise
        except Exception as e:  # сеть/JSON
            last = e
            time.sleep(pause * (attempt + 1))
    raise HyperliquidError(f"Hyperliquid недоступен: {last}")


def get_spot_prices(proxy: str | None = None) -> dict[str, float]:
    """Цены spot-токенов HL в USDC по midPx канонических пар TOKEN/USDC (кэш 60 с)."""
    return dict(_PRICES.get("spot", lambda: _load_spot_prices(proxy)))


def _load_spot_prices(proxy: str | None) -> dict[str, float]:
    data = _post({"type": "spotMetaAndAssetCtxs"}, proxy)
    if not (isinstance(data, list) and len(data) == 2):
        raise HyperliquidError("spotMetaAndAssetCtxs: неожиданная схема")
    meta, ctxs = data
    tokens = {t["index"]: t["name"] for t in meta.get("tokens", [])}
    # ctxs НЕ выровнены с universe по позиции (ctxs длиннее и идут в другом
    # порядке) — zip() раздавал токенам цены чужих пар (спам MAX → $10 вместо
    # $0.0000003). Сопоставляем строго по имени пары: pair.name == ctx.coin.
    ctx_by_coin = {c.get("coin"): c for c in ctxs if isinstance(c, dict) and c.get("coin")}
    prices: dict[str, float] = {"USDC": 1.0}
    for pair in meta.get("universe", []):
        idx = pair.get("tokens") or []
        if len(idx) != 2 or idx[1] != 0:  # котировка не в USDC
            continue
        base = tokens.get(idx[0])
        ctx = ctx_by_coin.get(pair.get("name"))
        if not base or ctx is None:
            continue
        px = ctx.get("midPx") or ctx.get("markPx")
        if px is not None:
            try:
                prices.setdefault(base, float(px))
            except (TypeError, ValueError):
                pass
    return prices


def _f(x: Any) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        raise HyperliquidError(f"нечисловое значение в ответе HL: {x!r}")


def get_positions(address: str, proxy: str | None = None) -> dict[str, Any]:
    """Позиции адреса на Hyperliquid в USD.

    Возвращает {"total_usd", "perp_usd", "spot_usd", "stake_usd",
                "details": [{"type","symbol","amount","value"}]}.
    Бросает HyperliquidError при недоступности/неожиданной схеме.
    """
    addr = address.lower()
    r = run_parallel({
        "perp": lambda: _post({"type": "clearinghouseState", "user": addr}, proxy),
        "spot": lambda: _post({"type": "spotClearinghouseState", "user": addr}, proxy),
        "stake": lambda: _post({"type": "delegatorSummary", "user": addr}, proxy),
    })
    perp, spot, stake = unwrap(r["perp"]), unwrap(r["spot"]), unwrap(r["stake"])
    if not isinstance(perp, dict) or "marginSummary" not in perp:
        raise HyperliquidError("clearinghouseState: неожиданная схема")
    if not isinstance(spot, dict) or "balances" not in spot:
        raise HyperliquidError("spotClearinghouseState: неожиданная схема")
    if not isinstance(stake, dict) or "delegated" not in stake:
        raise HyperliquidError("delegatorSummary: неожиданная схема")

    details: list[dict] = []
    perp_usd = _f(perp["marginSummary"].get("accountValue"))
    if perp_usd > 0:
        details.append({"type": "Perpetuals", "symbol": "USDC", "amount": perp_usd, "value": perp_usd})

    balances = [b for b in spot.get("balances", []) if _f(b.get("total")) > 0]
    hype_amount = _f(stake.get("delegated")) + _f(stake.get("undelegated")) + _f(stake.get("totalPendingWithdrawal"))
    prices = get_spot_prices(proxy) if balances or hype_amount > 0 else {}

    spot_usd = 0.0
    for b in balances:
        coin = b.get("coin", "?")
        amount = _f(b.get("total"))
        price = prices.get(coin)
        if price is None:
            # Нет рыночной цены → оцениваем по entryNtl (стоимость входа), без неё — 0.
            entry = _f(b.get("entryNtl"))
            price = entry / amount if amount and entry else 0.0
        value = amount * price
        spot_usd += value
        details.append({"type": "Spot", "symbol": coin, "amount": amount, "value": value})

    stake_usd = 0.0
    if hype_amount > 0:
        stake_usd = hype_amount * prices.get("HYPE", 0.0)
        details.append({"type": "Staked", "symbol": "HYPE", "amount": hype_amount, "value": stake_usd})

    return {
        "total_usd": perp_usd + spot_usd + stake_usd,
        "perp_usd": perp_usd,
        "spot_usd": spot_usd,
        "stake_usd": stake_usd,
        "details": details,
    }
