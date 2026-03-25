from __future__ import annotations
import asyncio
import functools
from typing import TypeVar, Callable, Awaitable
from .exceptions import RetryExhaustedError

F = TypeVar("F", bound=Callable[..., Awaitable])

# Заменяемая для тестов функция sleep
_sleep = asyncio.sleep


def retry(attempts: int = 3, backoff: float = 2.0):
    """
    Декоратор для async-функций с экспоненциальной паузой между попытками.

    Args:
        attempts: максимальное количество попыток
        backoff: множитель паузы (пауза = backoff ** attempt_index секунд)
    """
    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            last_error: Exception | None = None
            for attempt in range(attempts):
                try:
                    return await fn(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < attempts - 1:
                        delay = backoff ** attempt
                        await _sleep(delay)
            raise RetryExhaustedError(attempts=attempts, last_error=last_error)
        return wrapper  # type: ignore
    return decorator
