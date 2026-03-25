from __future__ import annotations
import threading
from app.core.models import ProxyConfig


class ProxyRotator:
    """Round-robin ротатор прокси. Thread-safe. Возвращает None если список пустой."""

    def __init__(self, proxies: list[ProxyConfig]) -> None:
        self._proxies = proxies
        self._index = 0
        self._lock = threading.Lock()

    def next(self) -> ProxyConfig | None:
        if not self._proxies:
            return None
        with self._lock:
            proxy = self._proxies[self._index % len(self._proxies)]
            self._index += 1
            return proxy

    def is_empty(self) -> bool:
        return not self._proxies
