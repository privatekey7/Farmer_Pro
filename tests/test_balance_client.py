# tests/test_balance_client.py
"""Единственный источник балансов — Rabby; фабрика + per-thread кэш сессий."""
from __future__ import annotations

import threading

from app.integrations.balance_client import create_balance_client, get_balance_client
from app.integrations.rabby_client import RabbyClient


def test_create_returns_rabby_client():
    client = create_balance_client("http://proxy:8080")
    assert isinstance(client, RabbyClient)


def test_create_requires_proxy():
    import pytest
    with pytest.raises(ValueError):
        create_balance_client("")


def test_thread_cache_reuses_same_proxy():
    a = get_balance_client("http://proxy:8080")
    b = get_balance_client("http://proxy:8080")
    assert a is b  # та же TLS-сессия в рамках потока


def test_thread_cache_separates_proxies():
    a = get_balance_client("http://proxy:8080")
    b = get_balance_client("http://proxy:9090")
    assert a is not b


def test_thread_cache_is_per_thread():
    same = {}

    def _other():
        same["client"] = get_balance_client("http://proxy:8080")

    main_client = get_balance_client("http://proxy:8080")
    t = threading.Thread(target=_other)
    t.start()
    t.join()
    # curl_cffi Session не потокобезопасна — у другого потока своя сессия.
    assert same["client"] is not main_client
