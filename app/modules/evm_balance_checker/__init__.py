from __future__ import annotations
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator

from PySide6.QtCore import QObject, Signal

from app.core.base_module import BaseModule
from app.core.models import RunContext, Result, ResultStatus, ColumnDef
from app.integrations.balance_verifier import MIN_VALUE_DISPLAY, check_wallet
from app.integrations.proxy_utils import ProxyRotator

# Защита от фантомных балансов (с 11.09.2026 Rabby отдаёт для части адресов
# чужие данные) — в app/integrations/balance_verifier.py: итог собирается
# только из проверенных компонентов, агрегат Rabby — лишь контроль, баланс
# принимается при согласии двух независимых выборок, иначе UNVERIFIED.

# UNVERIFIED/ERROR перепроверяются позже: заражение Rabby «залипает» на
# адресе на десятки секунд, и через паузу кошелёк обычно проверяется чисто.
RECHECK_ROUNDS = 2
RECHECK_DELAY_SEC = 30

_STATUS = {
    "OK": ResultStatus.OK,
    "UNVERIFIED": ResultStatus.UNVERIFIED,
}


def _to_result(res: dict) -> Result:
    """dict из balance_verifier.check_wallet → строка таблицы."""
    address = res["address"]
    status = _STATUS.get(res.get("status", ""))
    if status is None:
        return Result(item=address, status=ResultStatus.ERROR, error=res.get("error") or "Unknown error")

    tokens_data = res["tokens_data"]
    protocols_data = res["protocols_data"]

    chain_usd: dict[str, float] = {}
    for row in tokens_data + protocols_data:
        if row.get("chain"):
            chain_usd[row["chain"]] = chain_usd.get(row["chain"], 0.0) + row["value"]
    chains = ", ".join(sorted(c for c, v in chain_usd.items() if v >= MIN_VALUE_DISPLAY))
    if chain_usd:
        best_chain = max(chain_usd, key=chain_usd.get)  # type: ignore[arg-type]
        top_chain_usd = f"{best_chain}: ${chain_usd[best_chain]:.0f}"
    else:
        top_chain_usd = ""

    return Result(
        item=address,
        status=status,
        data={
            "total_usd":      round(res["total_usd"], 2),
            "tokens_usd":     round(res["tokens_usd"], 2),
            "protocols_usd":  round(res["protocols_usd"], 2),
            "native_usd":     round(res["native_usd"], 2),
            "tokens":         len(tokens_data),
            "top_tokens":     ", ".join(f"{t['symbol']}(${t['value']:.2f})" for t in tokens_data[:3]),
            "active_chains":  chains,
            "chains":         chains,
            "top_chain_usd":  top_chain_usd,
            "_detail":        {"tokens_data": tokens_data, "protocols_data": protocols_data},
        },
        error=res.get("error") or None,
    )


def _check_wallet_sync(
    address: str,
    rotator: ProxyRotator,
    stop_event: threading.Event,
) -> Result:
    return _to_result(check_wallet(address, rotator, stop_event))


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
            ColumnDef(key="tokens_usd",     label="Tokens $",     fmt="${:.2f}", sort_type="numeric"),
            ColumnDef(key="protocols_usd",  label="DeFi $",       fmt="${:.2f}", sort_type="numeric"),
            ColumnDef(key="native_usd",     label="Native $",     fmt="${:.2f}", sort_type="numeric"),
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
        # Каждый кошелёк держит в полёте 2+ выборки через разные прокси, каждая —
        # 5–15 запросов к Rabby. Замер на 100 прокси: 30 кошельков одновременно —
        # без 429 и с минимумом UNVERIFIED; 60 — почти не быстрее, UNVERIFIED
        # вдвое больше. Поэтому не больше ~1 кошелька на 3 прокси.
        if proxies:
            concurrency = min(concurrency, max(4, len(proxies) // 3))
        semaphore = asyncio.Semaphore(concurrency)
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="evm-wallet")

        async def _indexed_check(idx: int, addr: str) -> tuple[int, Result]:
            result = Result(item=addr, status=ResultStatus.ERROR, error="Stopped")
            for attempt in range(RECHECK_ROUNDS + 1):
                if attempt:
                    # Пауза вне семафора: слот занимают другие кошельки.
                    for _ in range(int(RECHECK_DELAY_SEC * 2)):
                        if self._stop_event.is_set():
                            return idx, result
                        await asyncio.sleep(0.5)
                async with semaphore:
                    if self._stop_event.is_set():
                        return idx, result
                    result = await loop.run_in_executor(
                        executor, _check_wallet_sync, addr, rotator, self._stop_event
                    )
                if result.status not in (ResultStatus.UNVERIFIED, ResultStatus.ERROR) or rotator.is_empty():
                    break
            return idx, result

        tasks = [asyncio.create_task(_indexed_check(i, addr)) for i, addr in enumerate(wallets)]
        buffer: dict[int, Result] = {}
        next_idx = 0
        try:
            for fut in asyncio.as_completed(tasks):
                if self._stop_event.is_set():
                    fut.close()  # as_completed yields coroutines; close unawaited one
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
