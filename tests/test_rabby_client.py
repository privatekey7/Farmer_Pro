# tests/test_rabby_client.py
"""Поведение RabbyClient: мокается только HTTP-граница (curl_cffi Session).

Покрывает фикс инцидента с фейковым 429 (см. docs/incident-429-antibot.md
в DeBankChecker): заголовки байт-в-байт как клиент Rabby, магазин ротации
ключей на процесс, одно-запросный cache_token_list с фолбэком.
"""
from __future__ import annotations

import pytest

import app.integrations.rabby_client as rabby_module
from app.integrations.rabby_client import (
    API_KEY_INIT,
    API_KEY_INIT_TIME,
    RabbyClient,
)

ADDRESS = "0x9F5Dc2f69006FFFae20247A95F1DFa0Cb057bCe9"  # checksum-регистр намеренно
ADDRESS_LC = ADDRESS.lower()

TOTAL_BALANCE = {
    "total_usd_value": 380.0,
    "chain_list": [
        {"id": "eth", "usd_value": 371.32},
        {"id": "arb", "usd_value": 8.68},
        {"id": "op", "usd_value": 0},          # нулевая сеть — токены не запрашиваем
        {"id": None, "usd_value": 5.0},        # битая запись — игнорируем
    ],
}
ETH_TOKENS = [
    {"id": "eth", "chain": "eth", "symbol": "ETH", "amount": 0.1, "price": 3600.0},
    {"id": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "chain": "eth",
     "symbol": "USDC", "amount": 11.32, "price": 1.0},
]
ARB_TOKENS = [
    {"id": "arb", "chain": "arb", "symbol": "ETH", "amount": 0.0024, "price": 3600.0},
]
# cache_token_list отдаёт токены ВСЕХ сетей и с мусором — фильтр на нашей стороне.
CACHE_TOKENS = ETH_TOKENS + ARB_TOKENS + [
    {"id": "scam", "chain": "eth", "symbol": "SCAM", "amount": 1000, "price": 5.0,
     "is_scam": True},
    {"id": "dust", "chain": "arb", "symbol": "DUST", "amount": 1, "price": 1.0,
     "is_core": False},
]


class FakeResponse:
    def __init__(self, payload, headers=None, status=200):
        self._payload = payload
        self.headers = headers or {}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    """Запоминает все запросы; ошибочные пути задаётся через fail_paths."""

    def __init__(self, impersonate=None, proxies=None):
        self.impersonate = impersonate
        self.proxies = proxies
        self.requests: list[dict] = []
        self.extra_headers_next: dict = {}
        self.fail_paths: set[str] = set()

    def get(self, url, params=None, headers=None, timeout=None):
        params = params or {}
        self.requests.append({"url": url, "params": dict(params), "headers": dict(headers or {})})
        path = url.replace("https://api.rabby.io", "")
        resp_headers = self.extra_headers_next
        self.extra_headers_next = {}
        if path in self.fail_paths:
            # фейковый 429 анти-бота: пустое тело, но ротационный заголовок есть
            return FakeResponse({}, headers=resp_headers, status=429)
        if path == "/v1/user/total_balance":
            return FakeResponse(TOTAL_BALANCE, headers=resp_headers)
        if path == "/v1/user/cache_token_list":
            return FakeResponse(CACHE_TOKENS, headers=resp_headers)
        if path == "/v1/user/token_list":
            chain = params.get("chain_id")
            data = {"eth": ETH_TOKENS, "arb": ARB_TOKENS}.get(chain, [])
            return FakeResponse(data, headers=resp_headers)
        return FakeResponse({}, status=404)


@pytest.fixture
def fake_session(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(rabby_module.cffi_requests, "Session",
                        lambda impersonate=None, proxies=None: session)
    # Сброс магазинa ключей — ротация из одного теста не должна течь в другой.
    monkeypatch.setattr(rabby_module, "_KEY_STATE",
                        {"key": API_KEY_INIT, "time": API_KEY_INIT_TIME})
    return session


def _paths(session):
    return [r["url"].replace("https://api.rabby.io", "") for r in session.requests]


def test_requires_proxy():
    with pytest.raises(ValueError):
        RabbyClient(proxy="")


def test_get_tokens_uses_one_shot_cache_token_list(fake_session):
    """Основной путь: total_balance + ОДИН cache_token_list (без серийного token_list)."""
    client = RabbyClient(proxy="http://proxy:8080")
    tokens = client.get_tokens(ADDRESS)

    assert tokens == ETH_TOKENS + ARB_TOKENS  # скам и не-core отфильтрованы
    assert _paths(fake_session) == [
        "/v1/user/total_balance",
        "/v1/user/cache_token_list",
    ]
    total_req = fake_session.requests[0]
    assert total_req["params"] == {"id": ADDRESS_LC, "is_core": "true"}
    cache_req = fake_session.requests[1]
    assert cache_req["params"] == {"id": ADDRESS_LC}


def test_get_tokens_falls_back_to_per_chain_token_list(fake_session):
    """Сбой cache_token_list → по-сетевой token_list только для ненулевых сетей."""
    fake_session.fail_paths = {"/v1/user/cache_token_list"}
    client = RabbyClient(proxy="http://proxy:8080")
    tokens = client.get_tokens(ADDRESS)

    assert tokens == ETH_TOKENS + ARB_TOKENS
    assert _paths(fake_session) == [
        "/v1/user/total_balance",
        "/v1/user/cache_token_list",   # неудачная попытка кэша (429)
        "/v1/user/token_list",
        "/v1/user/token_list",
    ]
    token_chains = {r["params"]["chain_id"] for r in fake_session.requests[2:]}
    assert token_chains == {"eth", "arb"}  # op (нулевая) и None пропущены
    for r in fake_session.requests[2:]:
        assert r["params"]["id"] == ADDRESS_LC
        assert r["params"]["is_all"] == "false"


def test_get_tokens_empty_wallet_skips_token_requests(fake_session, monkeypatch):
    """Пустой chain_list → запросы токенов не выполняются (как у расширения)."""
    monkeypatch.setitem(TOTAL_BALANCE, "chain_list", [])
    client = RabbyClient(proxy="http://proxy:8080")

    assert client.get_tokens(ADDRESS) == []
    assert _paths(fake_session) == ["/v1/user/total_balance"]


def test_get_total_usd_reuses_snapshot_after_get_tokens(fake_session):
    client = RabbyClient(proxy="http://proxy:8080")
    client.get_tokens(ADDRESS)
    n_before = len(fake_session.requests)

    assert client.get_total_usd(ADDRESS) == 380.0
    assert len(fake_session.requests) == n_before  # без лишнего запроса


def test_get_total_usd_alone_fetches(fake_session):
    client = RabbyClient(proxy="http://proxy:8080")
    assert client.get_total_usd(ADDRESS) == 380.0
    assert _paths(fake_session) == ["/v1/user/total_balance"]


def test_repeated_get_tokens_are_independent_fetches(fake_session):
    """Корроборация требует НЕЗАВИСИМЫХ выборок — total_balance не кэшируется между ними."""
    client = RabbyClient(proxy="http://proxy:8080")
    client.get_tokens(ADDRESS)
    client.get_tokens(ADDRESS)
    assert _paths(fake_session).count("/v1/user/total_balance") == 2


def test_headers_identify_rabby_client_and_sign(fake_session):
    """Заголовки байт-в-байт как клиент Rabby (HAR расширения): кейсинг и состав."""
    client = RabbyClient(proxy="http://proxy:8080")
    client.get_total_usd(ADDRESS)
    headers = fake_session.requests[0]["headers"]

    assert headers["x-client"] == "Rabby"
    assert headers["x-version"] == rabby_module.CLIENT_VERSION
    assert headers["x-version"] == "0.94.2"
    # Подписные заголовки — строго lowercase; x-api-time — время ВЫДАЧИ ключа.
    assert headers["x-api-key"] == API_KEY_INIT
    assert headers["x-api-time"] == str(API_KEY_INIT_TIME)
    assert headers["x-api-ver"] == "v2"
    assert headers["x-api-nonce"].startswith("n_")
    assert len(headers["x-api-sign"]) == 64
    # Браузерные заголовки поверх impersonate-фингерпринта.
    assert headers["accept"] == "application/json, text/plain, */*"
    assert headers["accept-language"].startswith("ru")
    assert headers["dnt"] == "1"
    assert headers["sec-fetch-mode"] == "cors"
    assert headers["sec-fetch-site"] == "none"
    # DeBank-специфичных заголовков быть не должно.
    assert "X-API-Key" not in headers
    assert "X-API-Time" not in headers
    assert "account" not in headers
    assert "source" not in headers
    assert "Referer" not in headers


def test_api_key_rotates_from_x_set_api_key(fake_session):
    client = RabbyClient(proxy="http://proxy:8080")
    fake_session.extra_headers_next = {"x-set-api-key": "rotated-key"}
    client.get_total_usd(ADDRESS)

    client2 = RabbyClient(proxy="http://proxy:8080")
    client2._total_balance_snapshot.clear()
    client2.get_total_usd("0x" + "1" * 40)
    req = fake_session.requests[-1]
    assert req["headers"]["x-api-key"] == "rotated-key"
    # Время выдачи ротированного ключа — момент ротации, не init-значение.
    assert int(req["headers"]["x-api-time"]) != API_KEY_INIT_TIME


def test_api_key_rotation_survives_error_response(fake_session):
    """x-set-api-key читается ДО raise_for_status: ключ с 429-ответа не теряется."""
    client = RabbyClient(proxy="http://proxy:8080")
    fake_session.fail_paths = {"/v1/user/total_balance"}
    fake_session.extra_headers_next = {"x-set-api-key": "key-from-429"}

    with pytest.raises(RuntimeError, match="HTTP 429"):
        client.get_total_usd(ADDRESS)

    fake_session.fail_paths.clear()
    client2 = RabbyClient(proxy="http://proxy:8080")
    client2.get_total_usd(ADDRESS)
    assert fake_session.requests[-1]["headers"]["x-api-key"] == "key-from-429"


def test_total_usd_handles_missing_value(fake_session, monkeypatch):
    monkeypatch.setitem(TOTAL_BALANCE, "total_usd_value", None)
    client = RabbyClient(proxy="http://proxy:8080")
    assert client.get_total_usd(ADDRESS) == 0.0
