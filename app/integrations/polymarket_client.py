# app/integrations/polymarket_client.py
"""
Нативная проверка позиций Polymarket, которые Rabby (complex_app_list,
app id `polymarket`) с 11.09.2026 отдаёт с чужими суммами.

Polymarket хранит позиции не на EOA, а на proxy-кошельке пользователя
(Gnosis Safe, фабрика 0xaacFeEa03eb1561C4e67d661e40682Bd20E3541b на Polygon,
`computeProxyAddress(address)` — селектор 0xd600539a). Адрес детерминирован
(CREATE2), поэтому кэшируется на диске навсегда (.cache/polymarket_proxy.json).
Стоимость позиций — публичный data-api: GET /value?user=<proxy>, плюс
наличные USDC.e на proxy (balanceOf); оба запроса идут параллельно.

Любая ошибка → PolymarketError (fail-closed: выборка повторится).
"""
from __future__ import annotations

import time
from typing import Any

from app.core.diskcache import PersistentDict
from app.core.parallel import run_parallel, unwrap
from app.integrations import http_pool, onchain
from app.integrations.http_pool import PerKeyLimiter

APPCHAIN_RATE_PER_SEC = 4.0  # на один IP
APPCHAIN_TIMEOUT = 8

_LIMITER = PerKeyLimiter(APPCHAIN_RATE_PER_SEC)
_PROXY_WALLETS = PersistentDict("polymarket_proxy.json")

POLYGON_CHAIN_ID = 137
SAFE_FACTORY = "0xaacFeEa03eb1561C4e67d661e40682Bd20E3541b"
COMPUTE_PROXY_SELECTOR = "0xd600539a"  # keccak256("computeProxyAddress(address)")[:4]
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
DATA_API = "https://data-api.polymarket.com"


class PolymarketError(RuntimeError):
    pass


def proxy_wallet(address: str, proxy: str | None = None) -> str | None:
    """Адрес Polymarket-proxy для EOA (None — нулевой адрес)."""
    addr = address.lower()
    if addr in _PROXY_WALLETS:
        return _PROXY_WALLETS.get(addr)
    data = COMPUTE_PROXY_SELECTOR + addr[2:].rjust(64, "0")
    try:
        res = onchain.call(POLYGON_CHAIN_ID, "eth_call", [{"to": SAFE_FACTORY, "data": data}, "latest"], proxy)
    except onchain.OnchainUnavailable as e:
        raise PolymarketError(f"Polygon RPC недоступен: {e}")
    if not isinstance(res, str) or len(res) < 42:
        raise PolymarketError(f"computeProxyAddress: неожиданный ответ {res!r}")
    pw = "0x" + res[-40:]
    result = None if int(pw, 16) == 0 else pw
    _PROXY_WALLETS.set(addr, result)
    return result


def _get_json(path: str, params: dict, proxy: str | None) -> Any:
    attempts, pause = http_pool.retry_policy(proxy)
    last: Exception | None = None
    for attempt in range(attempts):
        _LIMITER.wait(proxy)
        try:
            resp = http_pool.get(DATA_API + path, proxy, params=params, timeout=APPCHAIN_TIMEOUT)
            if resp.status_code == 429:
                time.sleep(pause if proxy else min(http_pool.retry_after(resp, 3.0 * (attempt + 1)), 30.0))
                last = PolymarketError("HTTP 429 from Polymarket")
                continue
            if resp.status_code != 200:
                raise PolymarketError(f"Polymarket HTTP {resp.status_code} ({path})")
            return resp.json()
        except (PolymarketError, http_pool.ProxyDead):
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(pause * (attempt + 1))
    raise PolymarketError(f"Polymarket недоступен: {last}")


def _cash(pw: str, proxy: str | None) -> float:
    try:
        return onchain.token_amount(POLYGON_CHAIN_ID, USDC_E, pw, 6, False, proxy)
    except onchain.OnchainUnavailable as e:
        raise PolymarketError(f"баланс USDC.e на proxy не проверен: {e}")


def get_positions(address: str, proxy: str | None = None) -> dict[str, Any]:
    """{"total_usd", "details": [...], "proxy_wallet"} для EOA."""
    addr = address.lower()
    pw = proxy_wallet(addr, proxy)
    if not pw:
        return {"total_usd": 0.0, "details": [], "proxy_wallet": None}

    r = run_parallel({
        "value": lambda: _get_json("/value", {"user": pw}, proxy),
        "cash": lambda: _cash(pw, proxy),
    })
    value, cash = unwrap(r["value"]), unwrap(r["cash"])
    if not isinstance(value, list):
        raise PolymarketError("value: неожиданная схема")
    pos_usd = 0.0
    for row in value:
        try:
            pos_usd += float(row.get("value") or 0)
        except (TypeError, ValueError):
            raise PolymarketError(f"value: нечисловое значение {row!r}")

    details = []
    if pos_usd > 0:
        positions = _get_json("/positions", {"user": pw, "sizeThreshold": 0}, proxy)
        for p in positions if isinstance(positions, list) else []:
            details.append({"type": "Position", "symbol": str(p.get("title") or p.get("asset") or "?")[:40],
                            "amount": float(p.get("size") or 0), "value": float(p.get("currentValue") or 0)})
    if cash > 0:
        details.append({"type": "Cash", "symbol": "USDC.e", "amount": cash, "value": cash})
    return {"total_usd": pos_usd + cash, "details": details, "proxy_wallet": pw}
