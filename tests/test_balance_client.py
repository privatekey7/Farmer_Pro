# tests/test_balance_client.py
"""Переключатель BALANCE_SOURCE: env → config.yaml → rabby (по умолчанию)."""
from __future__ import annotations

import pytest

from app.core.config import Config
from app.integrations.balance_client import create_balance_client, get_balance_source
from app.integrations.debank_client import DeBankClient
from app.integrations.rabby_client import RabbyClient


class StubConfig:
    """Config-стаб: не трогает config.yaml на диске."""

    def __init__(self, data=None, env=None):
        self._data = data or {}
        self._env = env or {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def env(self, key, default=None):
        return self._env.get(key, default)


@pytest.fixture
def config(monkeypatch):
    def _install(data=None, env=None):
        stub = StubConfig(data, env)
        monkeypatch.setattr(Config, "_instance", stub)
        return stub
    yield _install
    Config._instance = None


def test_default_source_is_rabby(config):
    config()
    assert get_balance_source() == "rabby"
    assert isinstance(create_balance_client("http://proxy:8080"), RabbyClient)


def test_env_overrides_to_debank(config):
    config(data={"balance_source": "rabby"}, env={"BALANCE_SOURCE": "debank"})
    assert get_balance_source() == "debank"
    assert isinstance(create_balance_client("http://proxy:8080"), DeBankClient)


def test_config_yaml_source(config):
    config(data={"balance_source": "debank"})
    assert get_balance_source() == "debank"


def test_env_has_priority_over_config(config):
    config(data={"balance_source": "debank"}, env={"BALANCE_SOURCE": "rabby"})
    assert get_balance_source() == "rabby"


def test_invalid_source_falls_back_to_rabby(config):
    config(env={"BALANCE_SOURCE": "wat"})
    assert get_balance_source() == "rabby"


def test_case_and_whitespace_normalized(config):
    config(env={"BALANCE_SOURCE": "  DeBank "})
    assert get_balance_source() == "debank"
