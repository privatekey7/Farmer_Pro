from __future__ import annotations
import asyncio
import threading
from typing import AsyncIterator

from app.core.base_module import BaseModule
from app.core.models import RunContext, Result, ResultStatus, ProxyConfig
from app.integrations.pixelscan_client import check_quality

BATCH_SIZE = 50


async def _check_proxy_async(proxy: ProxyConfig, stop_event: threading.Event) -> Result:
    if stop_event.is_set():
        return Result(item=proxy.to_url(), status=ResultStatus.ERROR, error="Stopped")
    try:
        data = await check_quality(proxy)
        return Result(item=proxy.to_url(), status=ResultStatus.OK, data=data)
    except Exception as e:
        return Result(item=proxy.to_url(), status=ResultStatus.ERROR, error=str(e))


class ProxyCheckerModule(BaseModule):
    name = "Proxy Check"

    def __init__(self) -> None:
        from app.ui.module_views.proxy_checker_view import ProxyCheckerConfigWidget
        self._widget = ProxyCheckerConfigWidget()
        self._stop_event = threading.Event()

    def get_config_widget(self):
        return self._widget

    async def run(self, ctx: RunContext) -> AsyncIterator[Result]:
        self._stop_event.clear()
        proxies = self._widget.get_proxies()
        logger = ctx.extra.get("logger")
        total = len(proxies)

        for i in range(0, total, BATCH_SIZE):
            if self._stop_event.is_set():
                break
            batch = proxies[i : i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
            if logger:
                logger.info(
                    f"Batch {batch_num}/{total_batches} — checking {len(batch)} proxies ({i + 1}–{i + len(batch)} of {total})…"
                )
            results = await asyncio.gather(
                *[_check_proxy_async(p, self._stop_event) for p in batch]
            )
            for result in results:
                yield result

    async def stop(self) -> None:
        self._stop_event.set()

    def get_item_count(self) -> int:
        return len(self._widget.get_proxies())
