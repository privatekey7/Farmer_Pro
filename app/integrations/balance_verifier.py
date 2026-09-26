# app/integrations/balance_verifier.py
"""
Проверенный баланс EVM-кошелька через Rabby API (порт DeBankChecker v2.0.0,
docs/PHANTOM_BALANCES.md).

С 11.09.2026 бэкенд Rabby отдаёт для части адресов ЧУЖИЕ данные:
  * /v1/user/total_balance — агрегат с суммами чужого кошелька (одинаковые
    значения у разных адресов), «липнет» на минуты-часы — поэтому две выборки
    агрегата легко «сходятся» на фантоме;
  * /v1/user/complex_app_list (+ complex_protocol_list — EVM DeFi-позиции,
    стейкинг/LP/lending, без него они терялись) — случайные app-chain позиции (Hyperliquid,
    Lighter, Polymarket…) с нашим user_addr, но чужими суммами;
  * /v1/user/token_list, изредка cache_token_list — отдельные чужие токены,
    а иногда чужой список целиком.

Поэтому итог НИКОГДА не берётся из total_usd_value. Итог одной выборки =
  токены кошелька (cache_token_list, core, не скам; каждый ≥ ONCHAIN_MIN_USD
  подтверждён on-chain через публичные RPC)
  + EVM-позиции протоколов (пересчёт по asset_token_list, min(api, recalc))
  + app-chain позиции только из нативных API Hyperliquid / Lighter / Polymarket.
Агрегат Rabby — только контроль (aggregate_agrees + примечание).

Баланс принимается (OK), когда CORROBORATION_MIN_AGREE независимых выборок
(разные прокси) сошлись по итогу. В согласии не участвуют «заражённые»
выборки (on-chain проверка сняла заметную часть токенов) и «неполные» (нет
токена, подтверждённого на цепочке в другой выборке). Иначе — UNVERIFIED с
консервативным значением.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any

from app.core import diskcache
from app.core.parallel import Memo, run_parallel, unwrap
from app.integrations.http_pool import ProxyDead
from app.integrations.proxy_utils import ProxyRotator
from app.integrations.rabby_client import RabbyClient

logger = logging.getLogger(__name__)

MIN_VALUE_DISPLAY = 0.01

# Сверка компонентов с агрегатом Rabby.
COMPONENT_TOL_ABS = 1.0    # USD
COMPONENT_TOL_REL = 0.02   # 2%
PROTOCOL_CONFIRM_MAX = 8   # сколько протоколов подтверждать /v1/user/protocol за выборку
# Протоколы дешевле этого (и сверх PROTOCOL_CONFIRM_MAX) не перезапрашиваются —
# меньше запросов, меньше 429; принимаются, только если все проверенные
# позиции списка подтвердились.
PROTOCOL_CONFIRM_MIN_USD = 0.10
CHAIN_REFETCH_MAX = 6      # сколько сетей перепроверять свежим token_list, если агрегат по сети выше токенов

# On-chain проверка: токены ≥ ONCHAIN_MIN_USD подтверждаются
# eth_getBalance/balanceOf. Фантом (on-chain 0) отбрасывается, расхождение
# количества исправляется по сети. Токен, который не удалось подтвердить
# (сеть недоступна), срывает выборку — ничего непроверенного в итог не попадает.
ONCHAIN_MIN_USD = 0.5
ONCHAIN_AMOUNT_TOL = 0.01  # 1%

# Выборка не участвует в согласии, если on-chain проверка сняла больше
# max(TAINT_ABS_USD, TAINT_REL × стоимость токенов по Rabby).
TAINT_ABS_USD = 5.0
TAINT_REL = 0.10

CORROBORATION_MIN_AGREE = 2    # сошедшихся выборок нужно для приёма
CORROBORATION_MAX_FETCHES = 10  # бюджет выборок на кошелёк (включая хеджи)
CORROBORATION_REL_TOL = 0.02
CORROBORATION_ABS_TOL = 1.0
RETRY_ATTEMPTS = 10            # минимум попыток выборки (каждая — с новым прокси)
RETRY_PROXY_BACKOFF_SEC = 0.3  # следующий повтор идёт с другого IP — ждать почти не нужно
# Заражение Rabby «залипает» на адресе на ~10–20 с (соседние выборки через
# разные прокси получают те же чужие данные) — после заражённой/неполной
# выборки следующая запускается с паузой, чтобы выйти из этого окна.
TAINTED_BACKOFF_SEC = 4.0
PROXY_COOLDOWN_429_SEC = 15    # прокси с 429/403 от Rabby не выдаётся столько секунд
PROXY_COOLDOWN_TIMEOUT_SEC = 60
# Выборка, не завершившаяся за SNAPSHOT_HEDGE_SEC, дублируется новой через
# другой прокси — берутся первые согласованные.
SNAPSHOT_HEDGE_SEC = 5.0

# App-chain приложения Rabby, для которых есть нативная проверка.
APPCHAIN_NATIVE = {
    "hyperliquid": "hyperliquid",
    "lighter": "lighter",
    "lighter_robinhood": "lighter",
    "polymarket": "polymarket",
}
# Ключ нативного источника → (подпись в protocols_data, chain).
APPCHAIN_LABELS = {
    "hyperliquid": ("Hyperliquid", "hyperliquid"),
    "lighter": ("Lighter", "lighter"),
    "polymarket": ("Polymarket", "matic"),
}

_CHAINS_CACHE = "rabby_chains.json"
_CHAINS_TTL = 24 * 3600
_CHAIN_MEMO = Memo()


# ---------------------------------------------------------------- сети Rabby

def _chain_map(client: Any) -> dict[str, dict[str, Any]]:
    """rabby chain id → {"evm": community_id, "native": native_token_id}.

    Кэш на процесс (singleflight) и на диске на сутки. При сбое — {} (токены
    не пройдут on-chain проверку, выборка повторится).
    """
    try:
        return _CHAIN_MEMO.get("chains", lambda: _load_chain_map(client))
    except Exception:  # noqa: BLE001
        return {}


def _load_chain_map(client: Any) -> dict[str, dict[str, Any]]:
    chains = diskcache.load(_CHAINS_CACHE, ttl=_CHAINS_TTL)
    fresh = not isinstance(chains, list) or not chains
    if fresh:
        chains = client.get_chain_list()
    m: dict[str, dict[str, Any]] = {}
    for c in chains or []:
        try:
            m[str(c["id"])] = {"evm": int(c["community_id"]), "native": str(c.get("native_token_id") or c["id"])}
        except (KeyError, TypeError, ValueError):
            continue
    if not m:
        raise RuntimeError("пустой список сетей Rabby")
    if fresh:
        diskcache.save(_CHAINS_CACHE, [{"id": k, "community_id": v["evm"], "native_token_id": v["native"]}
                                       for k, v in m.items()])
    return m


# ---------------------------------------------------------------- on-chain

class OnchainCheckFailed(RuntimeError):
    """Токен нельзя подтвердить on-chain (нет RPC сети) — выборка не засчитывается."""


def _onchain_verify_token(token: dict, address: str, chain_map: dict[str, dict[str, Any]],
                          proxy: str | None = None) -> float:
    """Количество токена по данным сети. Бросает OnchainCheckFailed. Тесты подменяют."""
    from app.integrations import onchain

    chain = str(token.get("chain") or "")
    info = chain_map.get(chain)
    if not info:
        raise OnchainCheckFailed(f"сеть {chain!r} не сопоставлена с EVM chain id")
    token_id = str(token.get("id") or "")
    is_native = (token_id == chain or token_id == info["native"] or not token_id.startswith("0x"))
    try:
        decimals = int(token.get("decimals") or 18)
    except (TypeError, ValueError):
        decimals = 18
    try:
        return onchain.token_amount(info["evm"], token_id, address, decimals, is_native, proxy)
    except onchain.OnchainUnavailable as e:
        raise OnchainCheckFailed(str(e))


def _token_key(t: dict) -> tuple[str, str]:
    """Идентификатор токена: (сеть, адрес контракта или символ)."""
    return str(t.get("chain") or ""), str(t.get("id") or t.get("symbol") or "").lower()


def _verify_tokens(tokens: list[dict], address: str, chain_map: dict[str, dict[str, Any]],
                   notes: list[str], require: bool, proxy: str | None = None,
                   memo: Memo | None = None, stats: dict[str, Any] | None = None) -> list[dict]:
    """On-chain проверка токенов с оценкой ≥ ONCHAIN_MIN_USD (все токены — параллельно).

    Фантом (on-chain 0) отбрасывается, расхождение количества исправляется по
    сети. Если сеть недоступна (все RPC молчат) — OnchainCheckFailed: выборка
    целиком не засчитывается и повторяется. require=True — кандидаты из
    по-сетевого token_list: мелкие (< ONCHAIN_MIN_USD) не принимаются вовсе.
    memo — общий кэш кошелька: одинаковый токен в разных выборках проверяется
    один раз.

    stats (если передан) пополняется: "rejected_usd" — стоимость, которую
    on-chain проверка сняла (фантомы + завышенные количества), "confirmed" —
    ключи токенов, подтверждённых на цепочке на ≥ ONCHAIN_MIN_USD.
    """
    memo = memo if memo is not None else Memo()
    todo = [(i, t) for i, t in enumerate(tokens) if _token_value(t) >= ONCHAIN_MIN_USD]

    def check(t: dict) -> Any:
        return lambda: memo.get(("onchain",) + _token_key(t),
                                lambda: _onchain_verify_token(t, address, chain_map, proxy))

    amounts = run_parallel({i: check(t) for i, t in todo})
    out: list[dict] = []
    for i, t in enumerate(tokens):
        value = _token_value(t)
        if i not in amounts:
            if not require:
                out.append(t)
            continue
        label = f"{t.get('symbol', '?')}@{t.get('chain', '?')}"
        onchain_amount = amounts[i]
        if isinstance(onchain_amount, OnchainCheckFailed):
            raise OnchainCheckFailed(f"{label} ${value:.2f}: {onchain_amount}")
        onchain_amount = unwrap(onchain_amount)
        amount = float(t.get("amount") or 0)
        price = float(t.get("price") or 0)
        if stats is not None:
            stats["rejected_usd"] = stats.get("rejected_usd", 0.0) + max(0.0, amount - max(onchain_amount, 0.0)) * price
            if onchain_amount * price >= ONCHAIN_MIN_USD:
                stats.setdefault("confirmed", set()).add(_token_key(t))
        if onchain_amount <= 0:
            notes.append(f"{label} ${value:.2f}: on-chain баланс 0 — фантомный токен отброшен")
            continue
        if abs(onchain_amount - amount) > ONCHAIN_AMOUNT_TOL * max(abs(amount), abs(onchain_amount)):
            fixed = dict(t)
            fixed["amount"] = onchain_amount
            notes.append(f"{label}: количество {amount:.6g} → {onchain_amount:.6g} по данным сети")
            out.append(fixed)
        else:
            out.append(t)
    return out


# ---------------------------------------------------------------- helpers

def _token_ok(t: dict) -> bool:
    return (t.get("is_verified", True) and not t.get("is_scam", False)
            and not t.get("is_suspicious", False) and t.get("is_core", True)
            and t.get("is_wallet", True))


def _token_value(t: dict) -> float:
    try:
        return float(t.get("price") or 0) * float(t.get("amount") or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_native_asset(token: dict) -> bool:
    """Нативные активы: id == ключ сети (не hex), контракт пустой/нулевой."""
    token_id = str(token.get("id", "") or "").lower()
    contract = str(token.get("contract_address", "") or "").lower()
    if contract and contract != "0x0000000000000000000000000000000000000000":
        return False
    return not token_id.startswith("0x")


def _close(a: float, b: float, abs_tol: float = COMPONENT_TOL_ABS, rel_tol: float = COMPONENT_TOL_REL) -> bool:
    return abs(a - b) <= max(abs_tol, rel_tol * max(abs(a), abs(b)))


def _by_chain(tokens: list[dict]) -> dict[str, float]:
    """Сумма токенов по сетям (USD)."""
    out: dict[str, float] = {}
    for t in tokens:
        out[t.get("chain", "")] = out.get(t.get("chain", ""), 0.0) + _token_value(t)
    return out


def _safe_position_value(item: dict) -> float:
    """
    Стоимость EVM-позиции протокола с защитой от фантомных данных.

    - Пересчитывает стоимость из asset_token_list (без скам-токенов).
    - Пустой asset_token_list или все токены скам → 0.
    - min(api_value, recalc): для lending api_value (залог−долг) < recalc;
      для farming/common ≈ равны; фантом с recalc=0 → 0.
    """
    api_value = max(0.0, float((item.get("stats") or {}).get("net_usd_value") or 0))

    asset_tokens = item.get("asset_token_list") or []
    if not asset_tokens:
        return 0.0

    recalc = 0.0
    for t in asset_tokens:
        if t.get("is_verified", True) and not t.get("is_scam", False):
            recalc += _token_value(t)

    recalc = max(0.0, recalc)
    if recalc == 0.0:
        return 0.0

    return min(api_value, recalc)


def _merge_portfolio(apps: list[dict], protocols: list[dict]) -> list[dict]:
    """complex_app_list (app-позиции) + complex_protocol_list (EVM DeFi:
    стейкинг, LP, lending). Если протокол есть в обоих списках, его EVM-позиции
    берутся из complex_protocol_list, а из complex_app_list — только app-chain
    (без сети), чтобы одна позиция не посчиталась дважды."""
    proto_ids = {p.get("id") for p in protocols or [] if isinstance(p, dict)}
    merged = [p for p in protocols or [] if isinstance(p, dict)]
    for app in apps or []:
        if not isinstance(app, dict):
            continue
        if app.get("id") in proto_ids:
            items = [it for it in app.get("portfolio_item_list") or [] if not _item_chain(it)]
            if not items:
                continue
            app = {**app, "portfolio_item_list": items}
        merged.append(app)
    return merged


def _accept_evm_positions(client: Any, address: str, proxy: str | None, shared: Memo,
                          chain_map: dict[str, dict[str, Any]],
                          evm_positions: dict[tuple[str, str, str], float],
                          notes: list[str], protocols_usd: float, protocols_data: list[dict],
                          unverified_usd: float, confirmed_keys: set) -> tuple[float, list[dict], float, bool]:
    """Шаг 3b выборки (см. _fetch_snapshot): проверка EVM-позиций протоколов.

    Подтверждённые позиции ≥ PROTOCOL_CONFIRM_MIN_USD попадают в confirmed_keys: выборка,
    где такой позиции нет (Rabby отдал чужой список протоколов при своих
    токенах), считается неполной и в согласии не участвует — как с токенами.
    """
    pos_chains = sorted({ch for _, _, ch in evm_positions})
    active = run_parallel({ch: (lambda ch=ch: shared.get(("nonce", ch), lambda: _chain_active(
        address, ch, chain_map, proxy))) for ch in pos_chains})
    rejected: dict[tuple[str, str, str], str] = {}
    for ch in pos_chains:
        # Агрегат Rabby здесь не используется: через прокси он часто чужой и
        # отклонял настоящие позиции (а фейк с заражённым агрегатом пропускал).
        reason = "кошелёк не делал транзакций в этой сети" if unwrap_or_none(active[ch]) is False else ""
        if reason:
            for key in evm_positions:
                if key[2] == ch:
                    rejected[key] = reason

    # (2) Подтверждение независимым запросом — крупнейшие протоколы.
    by_proto: dict[str, float] = {}
    for key, v in evm_positions.items():
        if key not in rejected:
            by_proto[key[0]] = by_proto.get(key[0], 0.0) + v
    to_confirm = [pid for pid, v in sorted(by_proto.items(), key=lambda kv: -kv[1])
                  if v >= PROTOCOL_CONFIRM_MIN_USD][:PROTOCOL_CONFIRM_MAX]
    answers = run_parallel({pid: (lambda pid=pid: client.get_protocol(address, pid)) for pid in to_confirm})
    confirmed: dict[tuple[str, str], float] = {}
    for pid in to_confirm:
        proto = unwrap(answers[pid])  # сбой запроса → выборка повторится
        for item in proto.get("portfolio_item_list") or []:
            ch = _item_chain(item)
            if ch:
                confirmed[(pid, ch)] = confirmed.get((pid, ch), 0.0) + _safe_position_value(item)
    list_foreign = False
    for key, v in evm_positions.items():
        pid, _, ch = key
        if key in rejected or pid not in to_confirm:
            continue
        # Сумма по (протокол, сеть) — у протокола может быть несколько имён позиций.
        claimed = sum(x for k, x in evm_positions.items() if k[0] == pid and k[2] == ch)
        got = confirmed.get((pid, ch), 0.0)
        if not _close(claimed, got):
            rejected[key] = f"повторный запрос протокола дал ${got:.2f}"
            list_foreign = True
        else:
            if got < claimed:
                evm_positions[key] = v * got / claimed if claimed else 0.0
            if got >= PROTOCOL_CONFIRM_MIN_USD:
                confirmed_keys.add(("protocol", pid, ch))
    # Rabby подменяет ответ целиком, а не отдельные позиции: если все
    # проверенные (крупнейшие) позиции подтвердились, список свой и хвост
    # (мелочь и то, что не влезло в лимит запросов) принимается.
    for key in evm_positions:
        if key not in rejected and key[0] not in to_confirm and list_foreign:
            rejected[key] = "список протоколов Rabby признан чужим"

    for (pid, name, ch), v in evm_positions.items():
        if (pid, name, ch) in rejected:
            unverified_usd += v
            if round(v, 2) >= MIN_VALUE_DISPLAY:
                notes.append(f"{name} ({ch}) ${v:.2f} отклонена: {rejected[(pid, name, ch)]}")
            continue
        if round(v, 2) < MIN_VALUE_DISPLAY:
            continue
        protocols_usd += v
        protocols_data.append({"name": name, "chain": ch, "value": round(v, 2)})
    return protocols_usd, protocols_data, unverified_usd, list_foreign


def unwrap_or_none(v: Any) -> Any:
    return None if isinstance(v, BaseException) else v


def _chain_active(address: str, chain: str, chain_map: dict[str, dict[str, Any]],
                  proxy: str | None) -> bool | None:
    """Отправлял ли адрес транзакции в сети (nonce > 0). None — сеть не
    определена или RPC недоступен (тогда решает только сверка с агрегатом)."""
    from app.integrations import onchain

    evm = (chain_map.get(chain) or {}).get("evm")
    if not evm:
        return None
    try:
        nonce = onchain._hex_to_int(onchain.call(evm, "eth_getTransactionCount", [address.lower(), "latest"], proxy))
    except Exception:  # noqa: BLE001
        return None
    return nonce > 0


def _item_chain(item: dict) -> str:
    """Сеть позиции по токенам; '' для app-chain (Hyperliquid, Lighter, …)."""
    detail = item.get("detail") or {}
    for tk in (detail.get("supply_token_list") or []) + (item.get("asset_token_list") or []):
        if tk.get("chain"):
            return str(tk["chain"])
    return ""


def _agree(a: float, b: float) -> bool:
    """Две суммы считаются согласованными в пределах относ./абс. допуска."""
    return _close(a, b, CORROBORATION_ABS_TOL, CORROBORATION_REL_TOL)


def _largest_agreeing_cluster(snaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Наибольшая группа выборок, согласованных по total_usd (при равенстве — меньшая сумма)."""
    best: list[dict[str, Any]] = []
    for anchor in snaps:
        cluster = [s for s in snaps if _agree(s["total_usd"], anchor["total_usd"])]
        if (len(cluster) > len(best)
                or (len(cluster) == len(best) and best
                    and _rep(cluster)["total_usd"] < _rep(best)["total_usd"])):
            best = cluster
    return best


def _rep(cluster: list[dict[str, Any]]) -> dict[str, Any]:
    """Представитель группы: выборка с минимальным total_usd (все согласованы)."""
    return min(cluster, key=lambda s: s["total_usd"])


def _build_tokens_data(tokens: list[dict]) -> list[dict]:
    """Из сырых токенов Rabby строит tokens_data для таблицы/экспорта."""
    out = []
    for t in tokens:
        value = round(_token_value(t), 2)
        if value < MIN_VALUE_DISPLAY:
            continue
        out.append({
            "symbol": t.get("symbol", "?"),
            "chain": t.get("chain", "?"),
            "amount": t.get("amount", 0),
            "price": t.get("price", 0),
            "value": value,
        })
    out.sort(key=lambda x: x["value"], reverse=True)
    return out


def _appchain_positions(address: str, proxy: str | None) -> dict[str, dict[str, Any]]:
    """Позиции адреса на app-chain через нативные API (все три — параллельно).

    Любая ошибка API → исключение (fail-closed): выборка повторяется целиком.
    Тесты подменяют эту функцию.
    """
    from app.integrations import hyperliquid_client, lighter_client, polymarket_client

    r = run_parallel({
        "hyperliquid": lambda: hyperliquid_client.get_positions(address, proxy),
        "lighter": lambda: lighter_client.get_positions(address, proxy),
        "polymarket": lambda: polymarket_client.get_positions(address, proxy),
    })
    return {k: unwrap(v) for k, v in r.items()}


def _aggregate_optional(err: Exception) -> bool:
    """Можно ли продолжить выборку без агрегата: 403 (бан эндпоинта для IP).
    На 429 выборка повторится с другого IP, чтобы не терять сверку по сетям."""
    s = str(err)
    return "403" in s or "Forbidden" in s


# ---------------------------------------------------------------- выборка

def _fetch_snapshot(address: str, proxy: str, shared: Memo | None = None,
                    with_positions: bool = True) -> dict[str, Any]:
    """Одна выборка баланса кошелька.

    total_usd = tokens_usd + protocols_usd, где protocols_usd — только
    проверенные позиции. Агрегат Rabby (total_usd_value) сохраняется в
    aggregate_usd и сравнивается с итогом, но НЕ используется как итог.

    shared — кэш кошелька (нативные app-chain позиции и on-chain количества
    общие для всех выборок). with_positions=False — только токены кошелька
    (DeFi и app-chain не запрашиваются; нужно сборщику токенов).
    """
    shared = shared if shared is not None else Memo()
    client = RabbyClient(proxy=proxy)
    notes: list[str] = []

    # 1) Все независимые запросы — одновременно.
    calls: dict[str, Any] = {
        "agg": lambda: client.get_total_balance(address),
        "tokens": lambda: client.get_cache_token_list(address),
        "chain_map": lambda: _chain_map(client),
    }
    if with_positions:
        calls["apps"] = lambda: client.get_complex_app_list(address)
        calls["protocols"] = lambda: client.get_complex_protocol_list(address)
        calls["simple"] = lambda: client.get_simple_protocol_list(address)
        calls["native"] = lambda: shared.get("native", lambda: _appchain_positions(address, proxy))
    r = run_parallel(calls)
    dead = next((v for v in r.values() if isinstance(v, ProxyDead)), None)
    if dead:
        raise dead  # прокси мёртв — фолбэки через него бессмысленны

    # Агрегат Rabby — только для контроля и списка сетей.
    aggregate_usd: float | None = None
    agg: dict = {}
    if isinstance(r["agg"], Exception):
        if not _aggregate_optional(r["agg"]):
            raise r["agg"]
        notes.append(f"агрегат Rabby недоступен ({str(r['agg'])[:40]}) — контроль по агрегату пропущен")
    else:
        agg = r["agg"] or {}
        aggregate_usd = float(agg.get("total_usd_value") or 0.0)
    agg_chains: dict[str, float] = {}
    for c in agg.get("chain_list") or []:
        if isinstance(c, dict) and c.get("id"):
            agg_chains[str(c["id"])] = float(c.get("usd_value") or 0.0)

    # 2) Токены: все сети одним запросом (cache_token_list); при сбое —
    #    по-сетевой token_list по сетям из агрегата (без агрегата — повтор).
    if isinstance(r["tokens"], Exception):
        nonzero_chains = [c for c, v in agg_chains.items() if v > 0]
        if not nonzero_chains and not agg:
            raise r["tokens"]
        per_chain = run_parallel({c: (lambda c=c: client.get_token_list(address, chain_id=c))
                                  for c in nonzero_chains})
        tokens = [t for c in nonzero_chains for t in unwrap(per_chain[c])]
    else:
        tokens = r["tokens"]
    tokens = [t for t in tokens if _token_ok(t)]
    portfolio = _merge_portfolio(unwrap(r["apps"]), unwrap(r["protocols"])) if with_positions else []
    native = unwrap(r["native"]) if with_positions else {}
    chain_map = r["chain_map"] or {}

    # 3) Протоколы. EVM-позиции (есть chain у токенов) — пересчёт по asset_token_list.
    #    App-chain позиции (chain нет) из Rabby НЕ принимаются: их подтверждает
    #    нативный API (Hyperliquid, Lighter, Polymarket); прочие — в unverified_usd.
    protocols_data: list[dict] = []
    protocols_usd = 0.0
    proto_by_chain: dict[str, float] = {}
    unverified_usd = 0.0
    claimed_appchain: dict[str, float] = {}
    unverified_apps: dict[str, float] = {}
    evm_positions: dict[tuple[str, str, str], float] = {}  # (id, имя, сеть) → USD, до проверки
    for proto in portfolio:
        app_id = str(proto.get("id") or "")
        items = proto.get("portfolio_item_list") or []
        evm_items = [it for it in items if _item_chain(it)]
        app_items = [it for it in items if not _item_chain(it)]

        if app_items:
            claimed = sum(max(0.0, float((it.get("stats") or {}).get("net_usd_value") or 0)) for it in app_items)
            claimed_appchain[app_id] = claimed_appchain.get(app_id, 0.0) + claimed
            if app_id not in APPCHAIN_NATIVE:
                unverified_apps[app_id] = unverified_apps.get(app_id, 0.0) + claimed

        if not evm_items:
            continue
        # Позиции разбиваются по сетям: каждую сеть подтверждаем отдельно (3b).
        for item in evm_items:
            ch = _item_chain(item)
            value = _safe_position_value(item)
            if value <= 0:
                continue
            proto_by_chain[ch] = proto_by_chain.get(ch, 0.0) + value
            key = (app_id, proto.get("name", "?"), ch)
            evm_positions[key] = evm_positions.get(key, 0.0) + value

    # 2b) On-chain проверка токенов (фантомы отбрасываются, количества сверяются)
    #     и — одновременно с ней — свежий token_list по сетям, где агрегат выше
    #     токенов + EVM-позиций (устаревший кэш или фантом агрегата). Список
    #     таких сетей угадывается по непроверенным токенам и уточняется после
    #     проверки; кандидаты из token_list принимаются только после on-chain
    #     подтверждения.
    def fetch_fresh(chain: str) -> Any:
        return lambda: client.get_token_list(address, chain_id=chain)

    def suspects_for(toks: list[dict]) -> list[str]:
        by = _by_chain(toks)
        return sorted((c for c, v in agg_chains.items()
                       if v > COMPONENT_TOL_ABS and v > by.get(c, 0.0) + proto_by_chain.get(c, 0.0)
                       and not _close(v, by.get(c, 0.0) + proto_by_chain.get(c, 0.0))),
                      key=lambda c: -agg_chains[c])

    stage: dict[Any, Any] = {("fresh", c): fetch_fresh(c) for c in suspects_for(tokens)[:CHAIN_REFETCH_MAX]}
    raw_tokens = tokens
    rabby_tokens_usd = sum(_token_value(t) for t in raw_tokens)
    stats: dict[str, Any] = {"rejected_usd": 0.0, "confirmed": set()}
    stage["verify"] = lambda: _verify_tokens(raw_tokens, address, chain_map, notes, require=False,
                                             proxy=proxy, memo=shared, stats=stats)
    fresh = run_parallel(stage)
    tokens = unwrap(fresh.pop("verify"))

    suspects = suspects_for(tokens)
    refetch = suspects[:CHAIN_REFETCH_MAX]
    missing = [c for c in refetch if ("fresh", c) not in fresh]
    fresh.update(run_parallel({("fresh", c): fetch_fresh(c) for c in missing}))

    have = {_token_key(t) for t in tokens}
    candidates: dict[str, list[dict]] = {}
    for chain in refetch:
        candidates[chain] = [t for t in unwrap(fresh[("fresh", chain)])
                             if _token_ok(t) and _token_key(t) not in have]
    chain_notes: dict[str, list[str]] = {c: [] for c in candidates}
    chain_stats: dict[str, dict[str, Any]] = {c: {} for c in candidates}
    confirmed = run_parallel({
        c: (lambda c=c: _verify_tokens(candidates[c], address, chain_map, chain_notes[c], require=True,
                                       proxy=proxy, memo=shared, stats=chain_stats[c]))
        for c in refetch if candidates[c]
    })
    for st in chain_stats.values():
        stats["confirmed"] |= st.get("confirmed", set())
    by_chain = _by_chain(tokens)
    phantom_chains: dict[str, float] = {}
    for chain in refetch:
        notes.extend(chain_notes.get(chain, []))
        accepted = unwrap(confirmed[chain]) if chain in confirmed else []
        if accepted:
            add_usd = sum(_token_value(t) for t in accepted)
            tokens = tokens + accepted
            by_chain[chain] = by_chain.get(chain, 0.0) + add_usd
            notes.append(f"{chain}: кэш токенов устарел, on-chain подтверждены токены на ${add_usd:.2f}")
        explained = by_chain.get(chain, 0.0) + proto_by_chain.get(chain, 0.0)
        if not _close(agg_chains[chain], explained) and agg_chains[chain] > explained:
            phantom_chains[chain] = agg_chains[chain] - explained
    for chain in suspects[CHAIN_REFETCH_MAX:]:
        phantom_chains[chain] = agg_chains[chain] - by_chain.get(chain, 0.0) - proto_by_chain.get(chain, 0.0)
    tokens_usd = sum(_token_value(t) for t in tokens)

    # 3b) EVM-позиции протоколов. complex_protocol_list, как и остальные
    #     эндпоинты Rabby, в части ответов отдаёт ЧУЖОЙ портфель (одинаковые
    #     суммы у разных кошельков; иногда вместе с заражённым агрегатом).
    #     Позиция принимается, только если:
    #       (1) кошелёк активен в её сети on-chain (nonce > 0);
    #       (2) независимый запрос /v1/user/protocol по этому протоколу
    #           вернул ту же сумму в той же сети (берётся меньшая).
    #     Агрегат Rabby как фильтр не годится: через прокси он часто чужой.
    #     Не подтвердилась хоть одна позиция из проверенных — весь список
    #     считается чужим, непроверенный остаток тоже отклоняется.
    protocols_foreign = False
    if evm_positions:
        protocols_usd, protocols_data, unverified_usd, protocols_foreign = _accept_evm_positions(
            client, address, proxy, shared, chain_map, evm_positions, notes, protocols_usd, protocols_data, unverified_usd,
            stats["confirmed"])
    # Полнота: протокол из независимого simple_protocol_list, которого нет в
    # complex_protocol_list, — complex пришёл чужим/пустым при своих токенах.
    if with_positions:
        have = {pid for pid, _, _ in evm_positions}
        missing = sorted(
            str(p.get("id")) for p in unwrap(r["simple"]) or []
            if isinstance(p, dict) and str(p.get("chain") or "") in chain_map
            and str(p.get("id")) not in APPCHAIN_NATIVE and str(p.get("id")) not in have
            and float(p.get("net_usd_value") or 0) >= PROTOCOL_CONFIRM_MIN_USD)
        if missing:
            protocols_foreign = True
            notes.append("список протоколов Rabby неполный (нет " + ", ".join(missing[:5])
                         + ") — выборка не участвует в согласии")

    # 3a) App-chain: нативные API — единственный источник значений.
    if with_positions:
        for key, (label, chain) in APPCHAIN_LABELS.items():
            value = float((native.get(key) or {}).get("total_usd") or 0.0)
            if round(value, 2) >= MIN_VALUE_DISPLAY:
                protocols_usd += value
                protocols_data.append({"name": f"{label} (native API)", "chain": chain, "value": round(value, 2)})
        native_total = {k: float((v or {}).get("total_usd") or 0.0) for k, v in native.items()}
        for app_id, claimed in claimed_appchain.items():
            nat = APPCHAIN_NATIVE.get(app_id)
            if nat and not _close(claimed, native_total.get(nat, 0.0)):
                notes.append(f"Rabby приписывает {app_id} ${claimed:.2f}, нативный API даёт "
                             f"${native_total.get(nat, 0.0):.2f} — данные Rabby отклонены")
    for app_id, claimed in unverified_apps.items():
        unverified_usd += claimed
        if claimed >= MIN_VALUE_DISPLAY:
            notes.append(f"app-chain позиция {app_id} ${claimed:.2f} не проверяется нативным API — в итог не входит")

    protocols_data.sort(key=lambda p: p["value"], reverse=True)

    # 4) Итог и контроль по агрегату. Без позиций (with_positions=False)
    #    агрегат несравним с итогом — контроль пропускается.
    total_usd = tokens_usd + protocols_usd
    aggregate_agrees = True if aggregate_usd is None or not with_positions else _close(aggregate_usd, total_usd)
    if not aggregate_agrees:
        detail = ", ".join(f"{c}: +${v:.2f}" for c, v in sorted(phantom_chains.items(), key=lambda kv: -kv[1])[:5])
        notes.append(f"агрегат Rabby ${aggregate_usd:.2f} отклонён: проверенные компоненты дают ${total_usd:.2f}"
                     + (f" (необъяснённые сети: {detail})" if detail else ""))

    return {
        "address": address,
        "total_usd": total_usd,
        "tokens_usd": tokens_usd,
        "protocols_usd": protocols_usd,
        "native_usd": sum(_token_value(t) for t in tokens if _is_native_asset(t)),
        "aggregate_usd": aggregate_usd,
        "aggregate_agrees": aggregate_agrees,
        "unverified_usd": unverified_usd,
        "tokens": tokens,
        "tokens_data": _build_tokens_data(tokens),
        "protocols_data": protocols_data,
        "proxy": proxy,
        "notes": notes,
        "onchain_rejected_usd": round(stats["rejected_usd"], 2),
        "_rabby_tokens_usd": rabby_tokens_usd,
        "_confirmed": frozenset(stats["confirmed"]),
        "_protocols_foreign": protocols_foreign,
    }


# ---------------------------------------------------------------- кошелёк

def _is_rate_limited(err: Exception) -> bool:
    s = str(err).lower()
    return "429" in s or "403" in s or "too many" in s or "forbidden" in s


def _is_tainted(snap: dict[str, Any]) -> bool:
    """On-chain проверка сняла заметную часть токенов → Rabby отдал для этого
    запроса чужой список (подмена целиком: наших токенов в нём может не быть)."""
    rejected = snap.get("onchain_rejected_usd", 0.0)
    return rejected > max(TAINT_ABS_USD, TAINT_REL * snap.get("_rabby_tokens_usd", 0.0))


def _eligible(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Выборки, которые могут участвовать в согласии: не заражённые (чужие
    токены или чужой список протоколов) и полные — содержат все токены и
    позиции, подтверждённые в любой выборке кошелька.
    On-chain проверка снимает лишние токены, но пропавший из ответа Rabby
    токен можно заметить только по другим выборкам."""
    clean = [s for s in snapshots if not _is_tainted(s) and not s.get("_protocols_foreign")]
    # «Известное» — только из чистых выборок: заражённая могла «подтвердить»
    # чужую позицию, и тогда все честные выборки выглядели бы неполными.
    known: set = set()
    for s in clean:
        known |= s.get("_confirmed", frozenset())
    return [s for s in clean if known <= s.get("_confirmed", frozenset())]


def _finalize(chosen: dict[str, Any], snapshots: list[dict[str, Any]], corroborated: bool) -> dict[str, Any]:
    """Сводит примечания всех выборок в выбранный результат и ставит статус."""
    notes: list[str] = []
    for s in snapshots:
        for n in s.get("notes", []):
            if n not in notes:
                notes.append(n)
    rejected = sum(1 for s in snapshots if not s.get("aggregate_agrees", True))
    if rejected:
        notes.insert(0, f"агрегат Rabby отклонён в {rejected} из {len(snapshots)} выборок")
    eligible_ids = {id(s) for s in _eligible(snapshots)}
    skipped = sum(1 for s in snapshots if id(s) not in eligible_ids)
    if skipped:
        notes.insert(0, f"отброшено выборок с чужим/неполным списком токенов Rabby: {skipped}")
    chosen = {k: v for k, v in chosen.items() if not k.startswith("_")}
    chosen["notes"] = notes
    chosen["snapshots"] = len(snapshots)
    chosen["corroborated"] = corroborated
    if corroborated:
        chosen["status"] = "OK"
        chosen["error"] = ""
    else:
        values = sorted(round(s["total_usd"], 2) for s in snapshots)
        chosen["status"] = "UNVERIFIED"
        chosen["error"] = f"баланс не подтверждён: выборки {values}, взято консервативное ${chosen['total_usd']:.2f}"
    return chosen


def _error_result(address: str, err_msg: str) -> dict[str, Any]:
    return {"address": address, "status": "ERROR", "error": err_msg, "notes": [], "snapshots": 0}


def check_wallet(address: str, rotator: ProxyRotator, stop_event: threading.Event | None = None,
                 with_positions: bool = True) -> dict[str, Any]:
    """Проверяет баланс одного кошелька с защитой от «фантомных» балансов.

    CORROBORATION_MIN_AGREE выборок запускаются одновременно через разные
    прокси; упавшая выборка сразу заменяется новой (другой прокси), а если
    выборка не завершилась за SNAPSHOT_HEDGE_SEC, параллельно запускается
    замена (хедж). Возвращает dict со status:
      * "OK"         — набралось CORROBORATION_MIN_AGREE согласованных выборок;
      * "UNVERIFIED" — бюджет исчерпан без согласия, консервативное значение
                       (наибольшая согласованная группа, при равенстве — меньшая сумма);
      * "ERROR"      — ни одной успешной выборки (поле error).
    """
    need = max(1, CORROBORATION_MIN_AGREE)
    max_fetches = max(1, CORROBORATION_MAX_FETCHES)
    max_attempts = max(RETRY_ATTEMPTS, max_fetches * 3)

    if rotator.is_empty():
        return _error_result(address, "Нет доступных прокси")

    snapshots: list[dict[str, Any]] = []
    pending: dict[Future, str] = {}
    started: dict[Future, float] = {}
    attempts = 0
    last_error: Exception | None = None
    # Кэш «поколения» выборок: нативные API и on-chain считаются один раз на
    # поколение. Хеджи зависших выборок начинают новое поколение, чтобы не
    # ждать тот же зависший запрос.
    memo = Memo()
    pool = ThreadPoolExecutor(max_workers=max_fetches, thread_name_prefix="evm-snap")

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    def budget_left() -> bool:
        return len(snapshots) + len(pending) < max_fetches and attempts < max_attempts

    def run_snapshot(proxy: str, shared: Memo, delay: float) -> dict[str, Any]:
        if delay > 0:
            time.sleep(delay)
        return _fetch_snapshot(address, proxy, shared, with_positions)

    def launch(delay: float = 0.0) -> bool:
        nonlocal attempts
        proxy = rotator.next()
        if proxy is None:
            return False
        url = proxy.to_url()
        attempts += 1
        fut = pool.submit(run_snapshot, url, memo, delay)
        pending[fut] = url
        started[fut] = time.monotonic() + delay
        return True

    def active() -> list[Future]:
        """Выборки в полёте, которые ещё не считаются зависшими."""
        now = time.monotonic()
        return [f for f in pending if now - started[f] < SNAPSHOT_HEDGE_SEC]

    def top_up(delay: float = 0.0) -> None:
        """Держит в полёте столько живых выборок, сколько не хватает до согласия."""
        want = max(1, need - len(_largest_agreeing_cluster(_eligible(snapshots))))
        while len(active()) < want and budget_left() and launch(delay):
            pass

    try:
        top_up()
        result: dict[str, Any] | None = None
        while pending:
            if stopped():
                return _error_result(address, "Stopped")
            now = time.monotonic()
            live = active()
            timeout = 0.5  # опрос stop_event
            if live and budget_left():
                timeout = max(0.05, min(timeout, min(started[f] + SNAPSHOT_HEDGE_SEC - now for f in live)))
            done, _ = wait(list(pending), timeout=timeout, return_when=FIRST_COMPLETED)
            if not done:
                if len(active()) < len(live) and budget_left():
                    memo = Memo()  # хедж: новое поколение, мимо зависших запросов
                    top_up()
                continue
            retry_after = 0.0
            for fut in done:
                proxy = pending.pop(fut)
                started.pop(fut, None)
                try:
                    snap = fut.result()
                    snapshots.append(snap)
                    if not any(s is snap for s in _eligible(snapshots)):
                        retry_after = max(retry_after, TAINTED_BACKOFF_SEC)
                except Exception as e:  # noqa: BLE001
                    last_error = e
                    err_str = str(e).lower()
                    if isinstance(e, ProxyDead):
                        rotator.cooldown(proxy, float("inf"))
                    elif "timeout" in err_str or "timed out" in err_str:
                        rotator.cooldown(proxy, PROXY_COOLDOWN_TIMEOUT_SEC)
                    elif _is_rate_limited(e):
                        rotator.cooldown(proxy, PROXY_COOLDOWN_429_SEC)
                    retry_after = max(retry_after, RETRY_PROXY_BACKOFF_SEC)
                    logger.debug("[%s] snapshot failed via %s: %s", address[:10], proxy.split("@")[-1], str(e)[:120])

            cluster = _largest_agreeing_cluster(_eligible(snapshots))
            if len(cluster) >= need:
                result = _finalize(_rep(cluster), snapshots, corroborated=True)
                break
            top_up(retry_after)

        if result is None and snapshots:
            cluster = _largest_agreeing_cluster(_eligible(snapshots) or snapshots)
            result = _finalize(_rep(cluster), snapshots, corroborated=False)
        if result is None:
            return _error_result(address, str(last_error) if last_error else "Unknown error")
        for n in result["notes"]:
            logger.info("[%s] %s", address[:10], n)
        return result
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
