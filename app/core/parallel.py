"""
Примитивы параллельности: параллельный запуск независимых вызовов и
singleflight-мемоизация («один вычисляет — остальные ждут его результат»).
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, Callable, Hashable


def run_parallel(calls: dict[Hashable, Callable[[], Any]]) -> dict[Hashable, Any]:
    """Выполняет вызовы одновременно; результат — {ключ: значение | исключение}.

    Исключения не бросаются, а возвращаются как значения — вызывающий решает,
    какие из них фатальны. Последний вызов выполняется в текущем потоке.
    Пул создаётся на каждый вызов, поэтому вложенные run_parallel не могут
    взаимно заблокироваться.
    """
    items = list(calls.items())
    out: dict[Hashable, Any] = {}
    if not items:
        return out
    *rest, (last_key, last_fn) = items
    pool = ThreadPoolExecutor(max_workers=len(rest)) if rest else None
    try:
        futures = {k: pool.submit(fn) for k, fn in rest} if pool else {}
        try:
            out[last_key] = last_fn()
        except Exception as e:  # noqa: BLE001
            out[last_key] = e
        for k, fut in futures.items():
            try:
                out[k] = fut.result()
            except Exception as e:  # noqa: BLE001
                out[k] = e
    finally:
        if pool:
            pool.shutdown(wait=False)
    return {k: out[k] for k, _ in items}


def first_success(fns: list[Callable[[], Any]], hedge_after: float) -> Any:
    """Запускает fns[0]; следующая функция стартует, когда предыдущие упали или
    не завершились за hedge_after секунд. Возвращает первый успешный результат;
    если упали все — пробрасывает последнее исключение."""
    pool = ThreadPoolExecutor(max_workers=max(1, len(fns)))
    try:
        pending: set[Future] = set()
        queue = list(fns)
        last_exc: BaseException | None = None
        while queue or pending:
            if queue and (not pending or last_exc is not None):
                pending.add(pool.submit(queue.pop(0)))
                last_exc = None
            done, pending = wait(pending, timeout=hedge_after if queue else None, return_when=FIRST_COMPLETED)
            if not done and queue:
                pending.add(pool.submit(queue.pop(0)))
                continue
            for fut in done:
                try:
                    return fut.result()
                except Exception as e:  # noqa: BLE001
                    last_exc = e
            if last_exc is not None and not queue and not pending:
                raise last_exc
        raise RuntimeError("first_success: нет функций")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def unwrap(value: Any) -> Any:
    """Значение из run_parallel: исключение пробрасывается."""
    if isinstance(value, BaseException):
        raise value
    return value


class Memo:
    """Потокобезопасный кэш с singleflight: одновременные запросы одного ключа
    выполняют функцию один раз, остальные ждут её результат.

    Ошибка не кэшируется: ждущие получают то же исключение, следующий вызов
    вычисляет заново. ttl=None — хранить до конца жизни объекта.
    """

    def __init__(self, ttl: float | None = None):
        self._ttl = ttl
        self._lock = threading.Lock()
        self._items: dict[Hashable, tuple[float, Future]] = {}

    def get(self, key: Hashable, fn: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            hit = self._items.get(key)
            if hit and (self._ttl is None or not hit[1].done() or now - hit[0] < self._ttl):
                fut, owner = hit[1], False
            else:
                fut, owner = Future(), True
                self._items[key] = (now, fut)
        if not owner:
            return fut.result()
        try:
            value = fn()
        except BaseException as e:
            with self._lock:
                if self._items.get(key, (0, None))[1] is fut:
                    del self._items[key]
            fut.set_exception(e)
            raise
        with self._lock:
            self._items[key] = (time.monotonic(), fut)
        fut.set_result(value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
