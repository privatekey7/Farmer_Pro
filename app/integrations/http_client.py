from __future__ import annotations
import httpx
from app.core.models import ProxyConfig


def build_client(proxy: ProxyConfig | None = None, timeout: float = 30.0) -> httpx.AsyncClient:
    """Создаёт httpx AsyncClient с настроенным прокси и таймаутом."""
    proxy_url = proxy.to_url() if proxy is not None else None
    return httpx.AsyncClient(
        proxy=proxy_url,
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
    )
