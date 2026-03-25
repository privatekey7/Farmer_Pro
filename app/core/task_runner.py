from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal, QObject

from app.core.models import Result, RunContext
from app.core.base_module import BaseModule

if TYPE_CHECKING:
    pass


class _Signals(QObject):
    on_result = Signal(object)   # Result
    on_log = Signal(str)
    on_finished = Signal()


class TaskRunner(QThread):
    """
    Запускает модуль в отдельном потоке с собственным asyncio event loop.
    Concurrency управляется через asyncio.Semaphore(ctx.concurrency).
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._signals = _Signals()
        self.on_result = self._signals.on_result
        self.on_log = self._signals.on_log
        self.on_finished = self._signals.on_finished

        self._module: BaseModule | None = None
        self._ctx: RunContext | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._executor = ThreadPoolExecutor(max_workers=20)

    def submit(self, module: BaseModule, ctx: RunContext) -> None:
        """Запускает модуль. Вызывать из UI-потока."""
        if self.isRunning():
            # Останавливаем предыдущий run и ждём завершения потока
            if self._loop and self._module:
                asyncio.run_coroutine_threadsafe(self._do_stop(), self._loop)
            self.wait(5000)  # ждём до 5 сек

        self._module = module
        self._ctx = ctx
        self.start()

    def stop_module(self) -> None:
        """Останавливает текущий модуль. Вызывать из UI-потока."""
        if self._loop and self._module:
            asyncio.run_coroutine_threadsafe(self._do_stop(), self._loop)

    async def _do_stop(self) -> None:
        if self._module:
            await self._module.stop()

    def run(self) -> None:
        """Точка входа QThread. Создаёт asyncio loop и запускает задачи."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_module())
        finally:
            self._loop.close()
            self.on_finished.emit()

    async def _run_module(self) -> None:
        if self._module is None or self._ctx is None:
            return
        semaphore = asyncio.Semaphore(self._ctx.concurrency)
        self._ctx.extra["semaphore"] = semaphore
        try:
            async for result in self._module.run(self._ctx):
                self.on_result.emit(result)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.on_log.emit(f"[ERROR] Critical: {e}")
