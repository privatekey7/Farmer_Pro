# app/integrations/rabby_client.py
from __future__ import annotations
import threading
import time

import curl_cffi.requests as cffi_requests

from app.integrations.api_signer import RABBY_SIGN_PREFIX, sign_request

API_BASE = "https://api.rabby.io"

# Начальный x-api-key из HAR веб-версии Rabby; сервер ротирует через x-set-api-key.
API_KEY_INIT = "7cee6f31-6611-4821-beb8-6ca9e29ed965"

# Версия клиента Rabby, под которую записан HAR.
CLIENT_VERSION = "0.94.1"


class RabbyClient:
    """Клиент Rabby API (api.rabby.io). Прокси обязателен.

    Интерфейс совместим с ``DeBankClient`` (``get_tokens`` / ``get_total_usd``),
    поэтому модули работают с любым источником без изменений.

    Отличия от DeBank API (проверено HAR):
      - префикс подписи ``rabby-api`` (у DeBank — ``debank-api``);
      - идентификация через ``x-client: Rabby`` + ``x-version``
        (без ``account``/``source``/``Referer``);
      - параметр адреса — ``id`` (lowercase);
      - итог берётся из ``/v1/user/total_balance`` (``total_usd_value``) —
        готовый агрегат, ручного суммирования нет, что устраняет корневой
        сценарий фантомных балансов DeBank.
    """

    _api_key: str = API_KEY_INIT
    _api_key_lock: threading.Lock = threading.Lock()
    REQUEST_TIMEOUT: float = 15.0

    def __init__(self, proxy: str, impersonate: str = "chrome124") -> None:
        if not proxy:
            raise ValueError("Прокси обязателен для Rabby API")
        self._init_ts = int(time.time())
        self._session = cffi_requests.Session(
            impersonate=impersonate,
            proxies={"https": proxy, "http": proxy},
        )
        # Снимок последнего total_balance: get_tokens ВСЕГДА запрашивает свежий
        # (иначе повторные выборки корроборации не были бы независимыми),
        # а следующий за ним get_total_usd читает тот же снимок, чтобы токены
        # и итог относились к одному ответу API (и не тратить лишний запрос).
        self._total_balance_snapshot: dict[str, dict] = {}

    def _build_headers(self, params: dict, method: str, path: str) -> dict:
        with RabbyClient._api_key_lock:
            api_key = RabbyClient._api_key
        sign = sign_request(params, method, path, prefix=RABBY_SIGN_PREFIX)
        return {
            "X-API-Key": api_key,
            "X-API-Time": str(self._init_ts),
            "x-api-ts": str(sign["ts"]),
            "x-api-nonce": sign["nonce"],
            "x-api-ver": sign["version"],
            "x-api-sign": sign["signature"],
            "x-client": "Rabby",
            "x-version": CLIENT_VERSION,
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        params = params or {}
        headers = self._build_headers(params, "GET", path)
        resp = self._session.get(
            API_BASE + path,
            params=params,
            headers=headers,
            timeout=self.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        new_key = resp.headers.get("x-set-api-key")
        if new_key:
            with RabbyClient._api_key_lock:
                RabbyClient._api_key = new_key
        data = resp.json()
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return data

    def _fetch_total_balance(self, address: str) -> dict:
        """Агрегированный баланс + разбивка по сетям (ОДИН свежий запрос).

        ``is_core=true`` отсекает скам/непроверенные токены — как галка в UI Rabby.
        """
        addr = address.lower()
        result = self._get(
            "/v1/user/total_balance",
            {"id": addr, "is_core": "true"},
        )
        result = result if isinstance(result, dict) else {}
        self._total_balance_snapshot[addr] = result
        return result

    def get_tokens(self, address: str) -> list:
        """Все core-токены кошелька по всем ненулевым сетям (свежая выборка).

        Формат токена совместим с DeBank: ``id``/``chain``/``symbol``/
        ``amount``/``price`` (нативные — ``id`` == ключ сети, не hex).
        """
        total = self._fetch_total_balance(address)
        chain_list = total.get("chain_list", [])
        if not isinstance(chain_list, list):
            return []
        tokens: list = []
        for chain in chain_list:
            if not isinstance(chain, dict):
                continue
            chain_id = chain.get("id")
            if not chain_id or (chain.get("usd_value") or 0) <= 0:
                continue
            result = self._get(
                "/v1/user/token_list",
                {"id": address.lower(), "chain_id": chain_id, "is_all": "false"},
            )
            if isinstance(result, list):
                tokens.extend(result)
        return tokens

    def get_total_usd(self, address: str) -> float:
        """Итог кошелька из готового агрегата Rabby (токены + DeFi).

        Использует снимок последнего ``get_tokens`` (тот же ответ API);
        если его нет — делает свежий запрос.
        """
        total = self._total_balance_snapshot.get(address.lower())
        if total is None:
            total = self._fetch_total_balance(address)
        try:
            return float(total.get("total_usd_value") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    # ── Быстрый путь (используется EVM Balance Checker) ─────────────────────
    def fetch_total_balance(self, address: str) -> dict:
        """Публичная «дешёвая» выборка: ОДИН запрос total_balance.

        Содержит и итог (``total_usd_value``), и разбивку по сетям
        (``chain_list``) — этого достаточно для корроборации, без token_list.
        """
        return self._fetch_total_balance(address)

    def get_token_list(self, address: str, chain_id: str) -> list:
        """Список core-токенов одной сети (один запрос)."""
        result = self._get(
            "/v1/user/token_list",
            {"id": address.lower(), "chain_id": chain_id, "is_all": "false"},
        )
        return result if isinstance(result, list) else []
