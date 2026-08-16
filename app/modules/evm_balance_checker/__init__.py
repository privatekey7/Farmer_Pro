from __future__ import annotations
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator

from PySide6.QtCore import QObject, Signal

from app.core.base_module import BaseModule
from app.core.models import RunContext, Result, ResultStatus, ColumnDef
from app.integrations.balance_client import get_balance_client
from app.integrations.proxy_utils import ProxyRotator

RETRY_ATTEMPTS = 6
MIN_VALUE_DISPLAY = 0.01
ZERO_ADDR = "0x0000000000000000000000000000000000000000"

# ── Anti-phantom corroboration ──────────────────────────────────────────────
# Под высокой параллельной нагрузкой API ~1 раз из 30 возвращает портфель
# ЧУЖОГО адреса. Ответ внутренне согласован, поэтому одиночная выборка фантом
# не ловит. Особенность: истинный баланс СТАБИЛЕН между запросами, фантом —
# СЛУЧАЙНЫЙ. Решение: принимаем баланс, когда >=2 независимые выборки (разные
# прокси) сходятся по total_usd.
#
# ВАЖНО (скорость): корроборация делается на ДЕШЁВОМ запросе total_balance
# (1 HTTP-запрос), а тяжёлый token_list запрашивается ОДИН раз и только для
# уже подтверждённого снимка. Раньше каждая выборка тянула total_balance +
# token_list по каждой сети (1+N запросов) × минимум 2 выборки.
CORROBORATION_MIN_AGREE = 2
CORROBORATION_TOL_PCT = 0.02
CORROBORATION_TOL_ABS = 1.0
CORROBORATION_MAX_FETCHES = 5

# Пул для «внутренней» параллельности одного кошелька: две первые проверки
# идут одновременно, токены по сетям — тоже параллельно.
_IO_POOL = ThreadPoolExecutor(max_workers=256, thread_name_prefix="evm-io")
# Ниже этой суммы token_list не запрашивается: детализация не нужна.
TOKEN_DETAIL_MIN_USD = MIN_VALUE_DISPLAY


def _asset_value_usd(token: dict) -> float:
    return (token.get("price", 0) or 0) * (token.get("amount", 0) or 0)


def _is_native_asset(token: dict) -> bool:
    """Нативные активы: id == ключ сети (не hex), контракт пустой/нулевой."""
    token_id = str(token.get("id", "") or "").lower()
    contract = str(token.get("contract_address", "") or "").lower()
    if contract and contract != ZERO_ADDR:
        return False
    return not token_id.startswith("0x")


def _cheap_probe(address: str, proxy_url: str) -> dict:
    """Одна независимая ДЕШЁВАЯ выборка: total_usd + разбивка по сетям.

    Один HTTP-запрос (Rabby ``/v1/user/total_balance``). Сессия
    переиспользуется на прокси в рамках потока → без TLS handshake.
    """
    payload = get_balance_client(proxy_url).fetch_total_balance(address)
    try:
        total = float(payload.get("total_usd_value") or 0.0)
    except (TypeError, ValueError):
        total = 0.0
    return {
        "total_usd": total,
        "chain_list": payload.get("chain_list") or [],
        "proxy": proxy_url,
    }


def _fetch_tokens(address: str, snapshot: dict) -> list:
    """Токены подтверждённого снимка: все сети ОДНИМ запросом.

    ``cache_token_list`` покрывает все сети сразу; серийный ``token_list``
    (по запросу на сеть) душится анти-ботом Rabby фейковым 429. При сбое
    кэша — фолбэк по-сетевым ``token_list`` (параллельно, пустые сети
    пропускаются).
    """
    proxy_url = snapshot["proxy"]
    client = get_balance_client(proxy_url)

    chains = [
        c.get("id") for c in snapshot.get("chain_list", [])
        if isinstance(c, dict) and c.get("id")
        and (c.get("usd_value") or 0) > TOKEN_DETAIL_MIN_USD
    ]
    if not chains:
        return []
    try:
        return client.get_cache_token_list(address)
    except Exception:
        pass                                   # фолбэк — ниже

    if len(chains) == 1:
        try:
            return get_token_list(address, chains[0])
        except Exception:
            return []

    # Параллельно по сетям — каждый поток берёт свою сессию из кэша.
    def _one(chain_id: str) -> list:
        try:
            return get_balance_client(proxy_url).get_token_list(address, chain_id)
        except Exception:
            return []

    tokens: list = []
    for part in _IO_POOL.map(_one, chains):
        tokens.extend(part or [])
    return tokens


def _values_agree(a: float, b: float) -> bool:
    diff = abs(a - b)
    if diff <= CORROBORATION_TOL_ABS:
        return True
    return diff <= CORROBORATION_TOL_PCT * max(abs(a), abs(b), 1.0)


def _agreeing_cluster(snapshots: list[dict]) -> list[dict] | None:
    best: list[dict] | None = None
    for anchor in snapshots:
        cluster = [s for s in snapshots if _values_agree(s["total_usd"], anchor["total_usd"])]
        if len(cluster) >= CORROBORATION_MIN_AGREE and (best is None or len(cluster) > len(best)):
            best = cluster
    return best


def _representative(cluster: list[dict]) -> dict:
    ordered = sorted(cluster, key=lambda s: s["total_usd"])
    return ordered[len(ordered) // 2]


def _conservative_pick(snapshots: list[dict]) -> dict:
    return min(snapshots, key=lambda s: s["total_usd"])


def _build_result(address: str, snapshot: dict, tokens: list, corroborated: bool) -> Result:
    total_usd = snapshot["total_usd"]

    tokens_data = [
        {
            "symbol": t.get("symbol", "?"),
            "chain":  t.get("chain", "?"),
            "amount": t.get("amount", 0),
            "price":  t.get("price", 0),
            "value":  round(_asset_value_usd(t), 2),
        }
        for t in tokens
        if round(_asset_value_usd(t), 2) >= MIN_VALUE_DISPLAY
    ]
    tokens_data.sort(key=lambda x: x["value"], reverse=True)

    # Сети и топ-сеть берём из chain_list снимка (уже есть, запросов не нужно).
    chain_usd: dict[str, float] = {}
    for c in snapshot.get("chain_list", []):
        if isinstance(c, dict) and c.get("id") and (c.get("usd_value") or 0) > MIN_VALUE_DISPLAY:
            chain_usd[str(c["id"])] = float(c.get("usd_value") or 0)
    if not chain_usd:
        for t in tokens_data:
            chain_usd[t["chain"]] = chain_usd.get(t["chain"], 0) + t["value"]

    chains = sorted(chain_usd)
    if chain_usd:
        best_chain = max(chain_usd, key=chain_usd.get)  # type: ignore[arg-type]
        top_chain_usd = f"{best_chain}: ${chain_usd[best_chain]:.0f}"
    else:
        top_chain_usd = ""

    native_usd = round(sum(_asset_value_usd(t) for t in tokens if _is_native_asset(t)), 2)
    top_tokens = ", ".join(f"{t['symbol']}(${t['value']:.2f})" for t in tokens_data[:3])

    return Result(
        item=address,
        status=ResultStatus.OK,
        data={
            "total_usd":      round(total_usd, 2),
            "native_usd":     native_usd,
            "top_tokens":     top_tokens,
            "active_chains":  ", ".join(chains),
            "top_chain_usd":  top_chain_usd,
            "verified":       "✓" if corroborated else "⚠",
            "_detail":        {"tokens_data": tokens_data},
        },
    )


def _check_wallet_sync(
    address: str,
    rotator: ProxyRotator,
    stop_event: threading.Event,
) -> Result:
    """Проверка одного кошелька: дешёвая корроборация + один сбор токенов."""
    last_error: Exception | None = None
    snapshots: list[dict] = []
    attempts = 0
    max_attempts = CORROBORATION_MAX_FETCHES + RETRY_ATTEMPTS

    def _probe_with_next_proxy() -> dict:
        proxy = rotator.next()
        if proxy is None:
            raise RuntimeError("Нет доступных прокси")
        return _cheap_probe(address, proxy.to_url())

    if rotator.is_empty():
        return Result(item=address, status=ResultStatus.ERROR, error="Нет доступных прокси")

    # Первые CORROBORATION_MIN_AGREE выборки — одновременно (экономия latency):
    # часть уходит в пул, последняя выполняется в текущем потоке.
    futures = [_IO_POOL.submit(_probe_with_next_proxy)
               for _ in range(max(0, CORROBORATION_MIN_AGREE - 1))]
    for run in [_probe_with_next_proxy] + [f.result for f in futures]:
        attempts += 1
        try:
            snapshots.append(run())
        except Exception as e:
            last_error = e

    while True:
        if stop_event.is_set():
            return Result(item=address, status=ResultStatus.ERROR, error="Stopped")

        cluster = _agreeing_cluster(snapshots)
        if cluster is not None:
            snap = _representative(cluster)
            tokens = _fetch_tokens(address, snap) if snap["total_usd"] > TOKEN_DETAIL_MIN_USD else []
            return _build_result(address, snap, tokens, corroborated=True)

        if attempts >= max_attempts or len(snapshots) >= CORROBORATION_MAX_FETCHES:
            break

        attempts += 1
        try:
            snapshots.append(_probe_with_next_proxy())
        except Exception as e:
            last_error = e

    # Бюджет исчерпан без согласия — консервативный выбор, помечаем «⚠».
    if snapshots:
        snap = _conservative_pick(snapshots)
        tokens = _fetch_tokens(address, snap) if snap["total_usd"] > TOKEN_DETAIL_MIN_USD else []
        return _build_result(address, snap, tokens, corroborated=False)

    return Result(
        item=address,
        status=ResultStatus.ERROR,
        error=str(last_error) if last_error else "Unknown error",
    )


class _EvmSignals(QObject):
    # Создаётся в __init__ (main thread) — никогда не в run()!
    run_complete = Signal(list, dict)


class EvmBalanceCheckerModule(BaseModule):
    name = "EVM Balance"

    def column_schema(self) -> list[ColumnDef]:
        return [
            ColumnDef(key="item",           label="Address",      width=200),
            ColumnDef(key="status",         label="Status"),
            ColumnDef(key="total_usd",      label="Total $",      fmt="${:.2f}", sort_type="numeric"),
            ColumnDef(key="native_usd",     label="Native $",     fmt="${:.2f}", sort_type="numeric"),
            ColumnDef(key="verified",       label="✓",            width=40),
            ColumnDef(key="top_tokens",     label="Top Tokens"),
            ColumnDef(key="active_chains",  label="Chains"),
            ColumnDef(key="top_chain_usd",  label="Top Chain"),
        ]

    def __init__(self) -> None:
        from app.ui.module_views.evm_balance_view import EvmBalanceConfigWidget
        self._signals = _EvmSignals()
        self._results: list[Result] = []
        self._details: dict[str, dict] = {}
        self._widget = EvmBalanceConfigWidget()
        self._signals.run_complete.connect(self._widget.on_run_complete)
        self._stop_event = threading.Event()

    def get_config_widget(self):
        return self._widget

    def get_item_count(self) -> int:
        return len(self._widget.get_wallets())

    def get_results(self) -> list[Result]:
        return list(self._results)

    async def run(self, ctx: RunContext) -> AsyncIterator[Result]:
        self._results.clear()
        self._details.clear()
        self._stop_event.clear()

        wallets = self._widget.get_wallets()
        proxies = self._widget.get_proxies()
        rotator = ProxyRotator(proxies)

        # Параллельность: ограничение — сеть, не CPU. Раньше использовался
        # дефолтный executor (max ~12-32 потока), и он был узким горлышком
        # независимо от ctx.concurrency. Теперь пул сразу под нужный размер.
        concurrency = max(1, min(int(ctx.concurrency or 16), 200, max(1, len(wallets))))
        semaphore = asyncio.Semaphore(concurrency)
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="evm-wallet")

        async def _indexed_check(idx: int, addr: str) -> tuple[int, Result]:
            async with semaphore:
                if self._stop_event.is_set():
                    return idx, Result(item=addr, status=ResultStatus.ERROR, error="Stopped")
                result = await loop.run_in_executor(
                    executor, _check_wallet_sync, addr, rotator, self._stop_event
                )
                return idx, result

        tasks = [asyncio.create_task(_indexed_check(i, addr)) for i, addr in enumerate(wallets)]
        buffer: dict[int, Result] = {}
        next_idx = 0
        try:
            for fut in asyncio.as_completed(tasks):
                if self._stop_event.is_set():
                    for t in tasks:
                        t.cancel()
                    break
                idx, result = await fut
                buffer[idx] = result
                while next_idx in buffer:
                    r = buffer.pop(next_idx)
                    detail = r.data.pop("_detail", {})
                    self._results.append(r)
                    self._details[r.item] = detail
                    yield r
                    next_idx += 1
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            self._signals.run_complete.emit(list(self._results), dict(self._details))

    async def stop(self) -> None:
        self._stop_event.set()
