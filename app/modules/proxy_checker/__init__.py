from __future__ import annotations
import asyncio
import threading
from typing import AsyncIterator

from app.core.base_module import BaseModule
from app.core.models import RunContext, Result, ResultStatus, ProxyConfig, ColumnDef
from app.integrations.pixelscan_client import check_quality

# Одновременных проверок. Без батчей: новый прокси стартует, как только
# освободилось место, а не когда закончится самый медленный в пачке.
CONCURRENCY = 100


async def _check_proxy_async(proxy: ProxyConfig, stop_event: threading.Event) -> Result:
    if stop_event.is_set():
        return Result(item=proxy.to_url(), status=ResultStatus.ERROR, error="Stopped")
    try:
        data = await check_quality(proxy)
        data["proxy_type"] = proxy.protocol.upper()
        return Result(item=proxy.to_url(), status=ResultStatus.OK, data=data)
    except Exception as e:
        return Result(item=proxy.to_url(), status=ResultStatus.ERROR, error=str(e) or type(e).__name__)


class ProxyCheckerModule(BaseModule):
    name = "Proxy Check"
    # Мёртвый прокси — результат проверки, а не сбой прогона.
    item_errors_are_failures = False

    def column_schema(self) -> list[ColumnDef]:
        return [
            ColumnDef(key="item",        label="Proxy",       width=220),
            ColumnDef(key="status",      label="Status"),
            ColumnDef(key="proxy_type",  label="Type"),
            ColumnDef(key="quality",     label="Quality"),
            ColumnDef(key="latency_ms",  label="Latency",     fmt="{} ms", sort_type="numeric"),
        ]

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

        if logger:
            logger.info(f"Checking {total} proxies ({min(CONCURRENCY, total)} at a time)…")

        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def _limited(proxy: ProxyConfig) -> Result:
            async with semaphore:
                return await _check_proxy_async(proxy, self._stop_event)

        tasks = [asyncio.create_task(_limited(p)) for p in proxies]
        alive = 0
        try:
            # Результаты — по мере готовности: таблица и прогресс не ждут медленных.
            for fut in asyncio.as_completed(tasks):
                if self._stop_event.is_set():
                    fut.close()  # as_completed yields coroutines; close unawaited one
                    break
                result = await fut
                alive += result.status == ResultStatus.OK
                yield result
        finally:
            for t in tasks:
                t.cancel()
        if logger and not self._stop_event.is_set():
            logger.info(f"Done: {alive} alive, {total - alive} dead")

    async def stop(self) -> None:
        self._stop_event.set()

    def get_item_count(self) -> int:
        return len(self._widget.get_proxies())
