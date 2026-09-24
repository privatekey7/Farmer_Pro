from __future__ import annotations
import ssl
import threading
import httpx
from app.core.models import ProxyConfig

# Общий SSL-контекст на процесс. httpx по умолчанию собирает новый контекст
# (загрузка сертификатов certifi) на КАЖДЫЙ клиент — ~0.3 с синхронной работы,
# с прокси ~0.85 с. Это блокировало event loop: при 50 одновременных проверках
# прокси секунды уходили на создание клиентов и попадали в latency.
_SSL_LOCK = threading.Lock()
_SSL_CONTEXT: ssl.SSLContext | None = None


def shared_ssl_context() -> ssl.SSLContext:
    global _SSL_CONTEXT
    with _SSL_LOCK:
        if _SSL_CONTEXT is None:
            _SSL_CONTEXT = httpx.create_ssl_context()
        return _SSL_CONTEXT


def build_client(proxy: ProxyConfig | None = None, timeout: float = 30.0) -> httpx.AsyncClient:
    """Создаёт httpx AsyncClient с настроенным прокси и таймаутом."""
    proxy_url = proxy.to_url() if proxy is not None else None
    return httpx.AsyncClient(
        proxy=proxy_url,
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        verify=shared_ssl_context(),
    )
