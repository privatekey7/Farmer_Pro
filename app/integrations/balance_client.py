# app/integrations/balance_client.py
from __future__ import annotations
import threading
from typing import Protocol

from app.core.config import Config
from app.integrations.debank_client import DeBankClient
from app.integrations.rabby_client import RabbyClient

"""Фабрика источника балансов EVM-кошельков.

DeBank под высокой параллельной нагрузкой отдаёт фантомные (чужие) портфели,
поэтому по умолчанию используется Rabby API (api.rabby.io) — готовый агрегат
``total_balance`` без ручного суммирования.

Переключатель ``BALANCE_SOURCE`` — мгновенный откат на DeBank без правки кода
(kill switch): env-переменная ``BALANCE_SOURCE=debank`` или ключ
``balance_source: debank`` в config.yaml. Env имеет приоритет.
"""

DEFAULT_BALANCE_SOURCE = "rabby"
_VALID_SOURCES = ("rabby", "debank")


class BalanceClient(Protocol):
    """Общий интерфейс DeBankClient / RabbyClient."""

    def get_tokens(self, address: str) -> list: ...

    def get_total_usd(self, address: str) -> float: ...


def get_balance_source() -> str:
    """Активный источник баланса: env ``BALANCE_SOURCE`` → config.yaml → rabby."""
    cfg = Config.instance()
    source = (
        cfg.env("BALANCE_SOURCE")
        or cfg.get("balance_source")
        or DEFAULT_BALANCE_SOURCE
    )
    source = str(source).strip().lower()
    return source if source in _VALID_SOURCES else DEFAULT_BALANCE_SOURCE


def create_balance_client(proxy: str) -> BalanceClient:
    """Клиент активного источника баланса. Прокси обязателен."""
    if get_balance_source() == "debank":
        return DeBankClient(proxy=proxy)
    return RabbyClient(proxy=proxy)


# ── Кэш клиентов на поток ───────────────────────────────────────────────────
# Создание клиента = новая TLS-сессия через прокси (медленный handshake).
# При проверке тысяч кошельков это была основная потеря времени, поэтому
# сессии переиспользуются. Кэш — per-thread (curl_cffi Session не
# рассчитан на одновременное использование из разных потоков).
_thread_local = threading.local()


def get_balance_client(proxy: str) -> BalanceClient:
    """Клиент для данного прокси с переиспользованием TLS-сессии в этом потоке."""
    cache = getattr(_thread_local, "clients", None)
    if cache is None:
        cache = {}
        _thread_local.clients = cache
    key = (get_balance_source(), proxy)
    client = cache.get(key)
    if client is None:
        client = create_balance_client(proxy)
        if len(cache) > 64:          # защита от неограниченного роста
            cache.clear()
        cache[key] = client
    return client
