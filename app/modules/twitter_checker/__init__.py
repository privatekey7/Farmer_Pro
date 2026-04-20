from __future__ import annotations
import asyncio
import threading
from typing import AsyncIterator

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from app.core.base_module import BaseModule
from app.core.models import RunContext, Result, ResultStatus
from app.integrations.proxy_utils import ProxyRotator
from app.integrations.twitter_client import (
    TwitterClient,
    TwitterTokenStatus,
)

RETRY_ATTEMPTS = 3


def _check_token_sync(
    token: str,
    rotator: ProxyRotator,
    stop_event: threading.Event,
) -> Result:
    """Module-level sync function for run_in_executor. Checks one auth_token."""
    last_error: str = "Unknown error"

    for _ in range(RETRY_ATTEMPTS):
        if stop_event.is_set():
            return Result(
                item=token,
                status=ResultStatus.ERROR,
                error="Stopped",
                data={"username": None, "account_status": "error"},
            )

        proxy = rotator.next()
        if proxy is None:
            return Result(
                item=token,
                status=ResultStatus.ERROR,
                error="No proxies available",
                data={"username": None, "account_status": "error"},
            )

        try:
            r = TwitterClient(proxy.to_url()).check_token(token)

            if r.status == TwitterTokenStatus.OK:
                return Result(
                    item=token,
                    status=ResultStatus.OK,
                    data={"username": r.username, "account_status": "ok"},
                )

            if r.status in (
                TwitterTokenStatus.INVALID,
                TwitterTokenStatus.SUSPENDED,
                TwitterTokenStatus.LOCKED,
            ):
                # Definitive answer — do not retry
                return Result(
                    item=token,
                    status=ResultStatus.SKIP,
                    error=r.status.value,
                    data={"username": None, "account_status": r.status.value},
                )

            last_error = "API error"
        except Exception as e:
            last_error = str(e)

    return Result(
        item=token,
        status=ResultStatus.ERROR,
        error=last_error,
        data={"username": None, "account_status": "error"},
    )


class _TwitterSignals(QObject):
    # Created in __init__ (main thread) for correct Qt thread affinity
    run_complete = Signal(list)  # list[Result]


class TwitterCheckerModule(BaseModule):
    name = "Twitter Checker"

    def __init__(self) -> None:
        from app.ui.module_views.twitter_checker_view import TwitterCheckerConfigWidget
        self._signals = _TwitterSignals()
        self._results: list[Result] = []
        self._widget = TwitterCheckerConfigWidget()
        self._signals.run_complete.connect(self._widget.on_run_complete)
        self._stop_event = threading.Event()

    def get_config_widget(self) -> QWidget:
        return self._widget

    def get_item_count(self) -> int:
        return len(self._widget.get_tokens())

    async def run(self, ctx: RunContext) -> AsyncIterator[Result]:
        self._results.clear()
        self._stop_event.clear()

        tokens = self._widget.get_tokens()
        proxies = self._widget.get_proxies()
        rotator = ProxyRotator(proxies)
        semaphore = asyncio.Semaphore(ctx.concurrency)
        loop = asyncio.get_running_loop()

        async def _indexed_check(idx: int, token: str) -> tuple[int, Result]:
            async with semaphore:
                result = await loop.run_in_executor(
                    None, _check_token_sync, token, rotator, self._stop_event
                )
                return idx, result

        tasks = [asyncio.create_task(_indexed_check(i, t)) for i, t in enumerate(tokens)]
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
                    self._results.append(r)
                    yield r
                    next_idx += 1
        finally:
            self._signals.run_complete.emit(list(self._results))

    async def stop(self) -> None:
        self._stop_event.set()
