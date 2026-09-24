from __future__ import annotations
import threading
import time

import httpx

from app.core.models import ProxyConfig

PROXY_CHECK_URL = "https://api.relay.link/"
PROXY_CHECK_TIMEOUT = 8.0


def is_proxy_alive(proxy_url: str, timeout: float = PROXY_CHECK_TIMEOUT) -> bool:
    """Работает ли прокси: любой HTTP-ответ через него = жив.

    Мёртвый — 407 / не подключается / таймаут: такой прокси не годится для
    отправки транзакций, где обрыв хуже отказа.
    """
    from app.integrations.http_client import shared_ssl_context

    try:
        with httpx.Client(proxy=proxy_url, timeout=timeout, verify=shared_ssl_context()) as client:
            client.get(PROXY_CHECK_URL)
        return True
    except Exception:
        return False


class ProxyRotator:
    """Round-robin ротатор прокси. Thread-safe. Возвращает None если список пустой.

    ``cooldown(url, seconds)`` временно исключает прокси из выдачи (429/таймаут,
    ``inf`` — мёртвый прокси). Если на паузе все, выдаётся тот, что освободится
    раньше остальных.
    """

    def __init__(self, proxies: list[ProxyConfig]) -> None:
        self._proxies = proxies
        self._index = 0
        self._lock = threading.Lock()
        self._cooldown_until: dict[str, float] = {}

    def next(self) -> ProxyConfig | None:
        if not self._proxies:
            return None
        with self._lock:
            now = time.monotonic()
            for _ in range(len(self._proxies)):
                proxy = self._proxies[self._index % len(self._proxies)]
                self._index += 1
                if self._cooldown_until.get(proxy.to_url(), 0.0) <= now:
                    return proxy
            return min(self._proxies, key=lambda p: self._cooldown_until.get(p.to_url(), 0.0))

    def cooldown(self, proxy_url: str, seconds: float) -> None:
        with self._lock:
            until = time.monotonic() + seconds
            self._cooldown_until[proxy_url] = max(self._cooldown_until.get(proxy_url, 0.0), until)

    def is_empty(self) -> bool:
        return not self._proxies

    def __len__(self) -> int:
        return len(self._proxies)
