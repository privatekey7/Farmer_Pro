from __future__ import annotations
import asyncio
import threading
from typing import AsyncIterator

from PySide6.QtCore import QObject, Signal

from app.core.base_module import BaseModule
from app.core.models import RunContext, Result, ResultStatus
from app.integrations.debank_client import DeBankClient
from app.integrations.proxy_utils import ProxyRotator

RETRY_ATTEMPTS = 10
MIN_VALUE_DISPLAY = 0.01


def _check_wallet_sync(
    address: str,
    rotator: ProxyRotator,
    stop_event: threading.Event,
) -> Result:
    """Sync функция для run_in_executor. Проверяет один кошелёк с retry."""
    last_error: Exception | None = None

    for _ in range(RETRY_ATTEMPTS):
        if stop_event.is_set():
            return Result(item=address, status=ResultStatus.ERROR, error="Stopped")

        proxy = rotator.next()
        if proxy is None:
            return Result(item=address, status=ResultStatus.ERROR,
                          error="Нет доступных прокси")
        try:
            client = DeBankClient(proxy=proxy.to_url())
            tokens = client.get_tokens(address)

            # --- Токены (только они — максимально быстрая проверка) ---
            tokens_usd = sum(t.get("price", 0) * t.get("amount", 0) for t in tokens)
            tokens_data = [
                {
                    "symbol": t.get("symbol", "?"),
                    "chain":  t.get("chain", "?"),
                    "amount": t.get("amount", 0),
                    "price":  t.get("price", 0),
                    "value":  round(t.get("price", 0) * t.get("amount", 0), 2),
                }
                for t in tokens
                if round(t.get("price", 0) * t.get("amount", 0), 2) >= MIN_VALUE_DISPLAY
            ]
            tokens_data.sort(key=lambda x: x["value"], reverse=True)

            total_usd = tokens_usd
            chains = sorted({t["chain"] for t in tokens_data})
            top_tokens = ", ".join(
                f"{t['symbol']}(${t['value']:.2f})" for t in tokens_data[:3]
            )

            return Result(
                item=address,
                status=ResultStatus.OK,
                data={
                    "total_usd":  round(total_usd, 2),
                    "tokens_usd": round(tokens_usd, 2),
                    "tokens":     len(tokens_data),
                    "chains":     ", ".join(chains),
                    "top_tokens": top_tokens,
                    "_detail":    {"tokens_data": tokens_data},
                },
            )
        except Exception as e:
            last_error = e

    return Result(
        item=address,
        status=ResultStatus.ERROR,
        error=str(last_error) if last_error else "Unknown error",
    )


class _EvmSignals(QObject):
    # _EvmSignals создаётся в __init__ (main thread) — никогда не создавать в run()!
    # Это обеспечивает Qt thread affinity для корректной queued-доставки из worker thread.
    run_complete = Signal(list, dict)


class EvmBalanceCheckerModule(BaseModule):
    name = "EVM Balance"

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
        """Для прогресс-бара MainWindow."""
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
        semaphore = asyncio.Semaphore(ctx.concurrency)
        loop = asyncio.get_running_loop()

        async def _indexed_check(idx: int, addr: str) -> tuple[int, Result]:
            async with semaphore:
                result = await loop.run_in_executor(
                    None, _check_wallet_sync, addr, rotator, self._stop_event
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
