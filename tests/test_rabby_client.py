# tests/test_rabby_client.py
"""Поведение RabbyClient: мокается только HTTP-граница (curl_cffi Session)."""
from __future__ import annotations
from urllib.parse import parse_qsl

import pytest

import app.integrations.rabby_client as rabby_module
from app.integrations.rabby_client import API_KEY_INIT, RabbyClient

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
    """Запоминает все запросы и отвечает по (path, chain_id)."""

    def __init__(self, impersonate=None, proxies=None):
        self.impersonate = impersonate
        self.proxies = proxies
        self.requests: list[dict] = []
        self.extra_headers_next: dict = {}

    def get(self, url, params=None, headers=None, timeout=None):
        params = params or {}
        self.requests.append({"url": url, "params": dict(params), "headers": dict(headers or {})})
        path = url.replace("https://api.rabby.io", "")
        resp_headers = self.extra_headers_next
        self.extra_headers_next = {}
        if path == "/v1/user/total_balance":
            return FakeResponse(TOTAL_BALANCE, headers=resp_headers)
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
    monkeypatch.setattr(RabbyClient, "_api_key", API_KEY_INIT)
    return session


def _paths(session):
    return [r["url"].replace("https://api.rabby.io", "") for r in session.requests]


def test_requires_proxy():
    with pytest.raises(ValueError):
        RabbyClient(proxy="")


def test_get_tokens_fetches_nonzero_chains_only(fake_session):
    client = RabbyClient(proxy="http://proxy:8080")
    tokens = client.get_tokens(ADDRESS)

    assert tokens == ETH_TOKENS + ARB_TOKENS
    assert _paths(fake_session) == [
        "/v1/user/total_balance",
        "/v1/user/token_list",
        "/v1/user/token_list",
    ]
    total_req = fake_session.requests[0]
    assert total_req["params"] == {"id": ADDRESS_LC, "is_core": "true"}
    token_chains = {r["params"]["chain_id"] for r in fake_session.requests[1:]}
    assert token_chains == {"eth", "arb"}  # op (нулевая) и None пропущены
    for r in fake_session.requests[1:]:
        assert r["params"]["id"] == ADDRESS_LC
        assert r["params"]["is_all"] == "false"


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
    client = RabbyClient(proxy="http://proxy:8080")
    client.get_total_usd(ADDRESS)
    headers = fake_session.requests[0]["headers"]

    assert headers["x-client"] == "Rabby"
    assert headers["x-version"] == rabby_module.CLIENT_VERSION
    assert headers["X-API-Key"] == API_KEY_INIT
    assert headers["x-api-ver"] == "v2"
    assert headers["x-api-nonce"].startswith("n_")
    assert len(headers["x-api-sign"]) == 64
    # DeBank-специфичных заголовков быть не должно
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
    assert fake_session.requests[-1]["headers"]["X-API-Key"] == "rotated-key"


def test_total_usd_handles_missing_value(fake_session, monkeypatch):
    monkeypatch.setitem(TOTAL_BALANCE, "total_usd_value", None)
    client = RabbyClient(proxy="http://proxy:8080")
    assert client.get_total_usd(ADDRESS) == 0.0
