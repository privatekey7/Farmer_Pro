from __future__ import annotations
import asyncio
import threading
from typing import AsyncIterator

from PySide6.QtCore import QObject, Signal

from app.core.base_module import BaseModule
from app.core.models import RunContext, Result, ResultStatus
from app.integrations.proxy_utils import ProxyRotator
from app.integrations.solana_rpc import SolanaClient

RETRY_ATTEMPTS = 3
MIN_VALUE_DISPLAY = 0.01


def _check_wallet_sync(
    address: str,
    rotator: ProxyRotator,
    rpc_url: str,
    stop_event: threading.Event,
) -> Result:
    """Sync worker — called via run_in_executor. rpc_url is a parameter, not a closure."""
    last_error: Exception | None = None

    for _ in range(RETRY_ATTEMPTS):
        if stop_event.is_set():
            return Result(item=address, status=ResultStatus.ERROR, error="Stopped")

        proxy = rotator.next()
        if proxy is None:
            return Result(item=address, status=ResultStatus.ERROR, error="Нет доступных прокси")

        try:
            data = SolanaClient(rpc_url, proxy.to_url()).get_wallet_data(address)

            tokens_data = [t for t in data.tokens if t["value"] >= MIN_VALUE_DISPLAY]
            tokens_data.sort(key=lambda x: x["value"], reverse=True)
            top_tokens = ", ".join(
                f"{t['symbol']}(${t['value']:.2f})" for t in tokens_data[:3]
            )

            return Result(
                item=address,
                status=ResultStatus.OK,
                data={
                    "sol_balance": round(data.sol_balance, 6),
                    "sol_usd":     round(data.sol_usd, 2),
                    "total_usd":   round(data.total_usd, 2),
                    "tokens":      len(tokens_data),
                    "top_tokens":  top_tokens,
                    "_detail":     {"tokens_data": tokens_data},
                },
            )
        except Exception as e:
            last_error = e

    return Result(
        item=address,
        status=ResultStatus.ERROR,
        error=str(last_error) if last_error else "Unknown error",
    )


class _SvmSignals(QObject):
    # Must be instantiated in __init__ on the main thread — never inside run()!
    # This ensures correct Qt thread affinity for queued signal delivery from worker threads.
    run_complete = Signal(list, dict)


class SvmBalanceCheckerModule(BaseModule):
    name = "SVM Balance"

    def __init__(self) -> None:
        from app.ui.module_views.svm_balance_view import SvmBalanceConfigWidget
        self._signals = _SvmSignals()
        self._results: list[Result] = []
        self._details: dict[str, dict] = {}
        self._widget = SvmBalanceConfigWidget()
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
        rpc_url = self._widget.get_rpc_url()
        # ctx.rpc_urls is intentionally ignored — RPC URL is widget-sourced
        rotator = ProxyRotator(proxies)
        semaphore = asyncio.Semaphore(ctx.concurrency)
        loop = asyncio.get_running_loop()

        async def _indexed_check(idx: int, addr: str) -> tuple[int, Result]:
            async with semaphore:
                result = await loop.run_in_executor(
                    None, _check_wallet_sync, addr, rotator, rpc_url, self._stop_event
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
            self._signals.run_complete.emit(list(self._results), dict(self._details))

    async def stop(self) -> None:
        self._stop_event.set()
