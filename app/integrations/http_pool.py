# app/integrations/http_pool.py
"""
Общий HTTP-слой проверки балансов: пул keep-alive сессий curl_cffi на каждый
исходящий IP (прокси) и token-bucket лимитеры «на IP».

Сессия берётся из пула на один запрос и возвращается после него, поэтому
TCP/TLS-соединение (и CONNECT-туннель прокси) переиспользуется между
запросами разных потоков, а одновременно одну сессию не использует никто
(curl_cffi Session не потокобезопасна).
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

import curl_cffi.requests as cffi_requests

IMPERSONATE = "chrome124"
_MAX_IDLE_PER_KEY = 32

_POOL_LOCK = threading.Lock()
_POOL: dict[str, list[cffi_requests.Session]] = {}


def _new_session(proxy: str | None) -> cffi_requests.Session:
    kw: dict[str, Any] = {"impersonate": IMPERSONATE}
    if proxy:
        kw["proxies"] = {"https": proxy, "http": proxy}
    return cffi_requests.Session(**kw)


@contextmanager
def session(proxy: str | None) -> Iterator[cffi_requests.Session]:
    """Сессия из пула для данного прокси. После ошибки сессия закрывается."""
    key = proxy or ""
    with _POOL_LOCK:
        stack = _POOL.get(key)
        s = stack.pop() if stack else None
    if s is None:
        s = _new_session(proxy)
    ok = False
    try:
        yield s
        ok = True
    finally:
        if ok:
            with _POOL_LOCK:
                stack = _POOL.setdefault(key, [])
                if len(stack) < _MAX_IDLE_PER_KEY:
                    stack.append(s)
                    s = None
        if s is not None:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass


class ProxyDead(RuntimeError):
    """Прокси отверг подключение (407 / CONNECT не удался) — повторять через него бессмысленно."""

    def __init__(self, proxy: str | None, msg: str):
        super().__init__(msg)
        self.proxy = proxy


def _is_proxy_failure(err: Exception) -> bool:
    s = str(err)
    return "CONNECT tunnel failed" in s or "response 407" in s


def request(method: str, url: str, proxy: str | None, **kw: Any) -> cffi_requests.Response:
    try:
        with session(proxy) as s:
            return s.request(method, url, **kw)
    except Exception as e:
        if proxy and _is_proxy_failure(e):
            raise ProxyDead(proxy, f"прокси недоступен: {str(e)[:120]}") from e
        raise


def get(url: str, proxy: str | None, **kw: Any) -> cffi_requests.Response:
    return request("GET", url, proxy, **kw)


def post(url: str, proxy: str | None, **kw: Any) -> cffi_requests.Response:
    return request("POST", url, proxy, **kw)


def retry_policy(proxy: str | None) -> tuple[int, float]:
    """(попыток, базовая пауза) для нативных API. Через прокси — быстро
    сдаёмся: выборку повторит check_wallet уже с другого IP."""
    return (2, 0.3) if proxy else (5, 1.0)


def retry_after(resp: cffi_requests.Response, default: float) -> float:
    try:
        return max(default, float(resp.headers.get("retry-after") or 0))
    except ValueError:
        return default


class RateLimiter:
    def __init__(self, per_sec: float):
        self._interval = 1.0 / max(per_sec, 0.1)
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self._interval
        if delay > 0:
            time.sleep(delay)


class PerKeyLimiter:
    """Отдельный RateLimiter на каждый ключ (исходящий IP: прокси или direct)."""

    def __init__(self, per_sec: float):
        self._per_sec = per_sec
        self._lock = threading.Lock()
        self._limiters: dict[str, RateLimiter] = {}

    def wait(self, key: str | None) -> None:
        k = key or "direct"
        with self._lock:
            lim = self._limiters.get(k)
            if lim is None:
                lim = self._limiters[k] = RateLimiter(self._per_sec)
        lim.wait()
