# app/integrations/balance_client.py
from __future__ import annotations
import threading

from app.integrations.rabby_client import RabbyClient

"""Фабрика клиентов балансов EVM-кошельков.

Источник балансов — Rabby API (api.rabby.io): готовый агрегат
``total_balance`` без ручного суммирования. Ветка DeBank выпилена
(api.debank.com под параллельной нагрузкой отдавал фантомные портфели
чужих адресов и душился анти-ботом); разбор — docs/incident-429-antibot.md
в DeBankChecker.
"""


def create_balance_client(proxy: str) -> RabbyClient:
    """Клиент Rabby API. Прокси обязателен."""
    return RabbyClient(proxy=proxy)


# ── Кэш клиентов на поток ───────────────────────────────────────────────────
# Создание клиента = новая TLS-сессия через прокси (медленный handshake).
# При проверке тысяч кошельков это была основная потеря времени, поэтому
# сессии переиспользуются. Кэш — per-thread (curl_cffi Session не
# рассчитан на одновременное использование из разных потоков).
_thread_local = threading.local()


def get_balance_client(proxy: str) -> RabbyClient:
    """Клиент для данного прокси с переиспользованием TLS-сессии в этом потоке."""
    cache = getattr(_thread_local, "clients", None)
    if cache is None:
        cache = {}
        _thread_local.clients = cache
    client = cache.get(proxy)
    if client is None:
        client = create_balance_client(proxy)
        if len(cache) > 64:          # защита от неограниченного роста
            cache.clear()
        cache[proxy] = client
    return client
