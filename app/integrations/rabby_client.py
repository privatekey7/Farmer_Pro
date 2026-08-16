# app/integrations/rabby_client.py
from __future__ import annotations
import threading
import time

import curl_cffi.requests as cffi_requests

from app.integrations.api_signer import RABBY_SIGN_PREFIX, sign_request

API_BASE = "https://api.rabby.io"

# Начальный x-api-key из HAR веб-версии Rabby; сервер ротирует через x-set-api-key.
API_KEY_INIT = "7cee6f31-6611-4821-beb8-6ca9e29ed965"

# Время ВЫДАЧИ init-ключа (из HAR расширения). x-api-time должен нести именно
# его, а не время запроса: во всех запросах сессии клиента значение одинаково
# и на ~23 млн секунд старше x-api-ts.
API_KEY_INIT_TIME = 1762656362

# Версия клиента Rabby, под которую записан HAR расширения.
CLIENT_VERSION = "0.94.2"

# Общий на процесс магазин ключа: клиенты кэшируются per-thread, но при
# ротации сервером ключ терять нельзя — иначе новые клиенты снова начнут с
# init-ключа. Все клиенты продолжают с последнего выданного ключа; время
# выдачи ротированного ключа — момент ротации.
_KEY_LOCK = threading.Lock()
_KEY_STATE: dict[str, object] = {"key": API_KEY_INIT, "time": API_KEY_INIT_TIME}


def _current_key() -> tuple[str, int]:
    with _KEY_LOCK:
        return _KEY_STATE["key"], _KEY_STATE["time"]  # type: ignore[index,return-value]


def _rotate_key(new_key: str) -> int:
    """Обновляет общий ключ; возвращает время выдачи действующего ключа."""
    with _KEY_LOCK:
        if new_key and new_key != _KEY_STATE["key"]:
            _KEY_STATE["key"] = new_key
            _KEY_STATE["time"] = int(time.time())
        return _KEY_STATE["time"]  # type: ignore[return-value]


def _is_core_token(token: dict) -> bool:
    """Фильтр скама/не-core — паритет с is_all=false у /v1/user/token_list."""
    return (
        token.get("is_verified", True)
        and not token.get("is_scam", False)
        and token.get("is_core", True)
    )


class RabbyClient:
    """Клиент Rabby API (api.rabby.io). Прокси обязателен.

    Отличия от старого DeBank-клиента (проверено HAR):
      - префикс подписи ``rabby-api`` (у DeBank был ``debank-api``);
      - идентификация через ``x-client: Rabby`` + ``x-version``
        (без ``account``/``source``/``Referer``);
      - параметр адреса — ``id`` (lowercase);
      - итог берётся из ``/v1/user/total_balance`` (``total_usd_value``) —
        готовый агрегат, ручного суммирования нет, что устраняет корневой
        сценарий фантомных балансов DeBank.

    Заголовки идентификации должны ТОЧНО повторять клиент Rabby (сверено с
    HAR браузерного расширения): подписные заголовки — в нижнем регистре,
    ``x-api-time`` — время выдачи ключа, поверх impersonate-фингерпринта
    досылаются браузерные заголовки. Анти-бот API на любое отклонение
    отвечает фейковым 429 с пустым телом при верной подписи (разбор инцидента
    — docs/incident-429-antibot.md в DeBankChecker).
    """

    REQUEST_TIMEOUT: float = 15.0

    def __init__(self, proxy: str, impersonate: str = "chrome124") -> None:
        if not proxy:
            raise ValueError("Прокси обязателен для Rabby API")
        self._api_key, self._key_time = _current_key()
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
        # Состав и кейсинг — строго по HAR расширения Rabby: отклонение
        # карается фейковым 429 с пустым телом.
        sign = sign_request(params, method, path, prefix=RABBY_SIGN_PREFIX)
        return {
            "accept": "application/json, text/plain, */*",
            "accept-language": "ru,ru-RU;q=0.9,en-US;q=0.8,en;q=0.7",
            "dnt": "1",
            "priority": "u=1, i",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "none",
            "sec-fetch-storage-access": "active",
            "x-api-key": self._api_key,
            "x-api-time": str(self._key_time),
            "x-api-ts": str(sign["ts"]),
            "x-api-nonce": sign["nonce"],
            "x-api-ver": sign["version"],
            "x-api-sign": sign["signature"],
            "x-client": "Rabby",
            "x-version": CLIENT_VERSION,
        }

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        params = params or {}
        headers = self._build_headers(params, "GET", path)
        resp = self._session.get(
            API_BASE + path,
            params=params,
            headers=headers,
            timeout=self.REQUEST_TIMEOUT,
        )

        # Ротация ключа читается ДО raise_for_status: сервер может выдать
        # новый ключ и вместе с ошибкой (429/403) — раньше он терялся.
        new_key = resp.headers.get("x-set-api-key")
        if new_key and new_key != self._api_key:
            self._key_time = _rotate_key(new_key)
            self._api_key = new_key

        resp.raise_for_status()

        data = resp.json()
        if isinstance(data, dict) and set(data.keys()) <= {"data", "error_code"}:
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

    def get_cache_token_list(self, address: str) -> list:
        """Токены кошелька по ВСЕМ сетям одним запросом (серверный кэш).

        Расширение Rabby само грузит токены этим эндпоинтом; серийный
        ``/v1/user/token_list`` (по запросу на сеть) — самый строгий пункт
        проверки анти-бота. Ответ отфильтрован от скама/не-core, чтобы состав
        не зависел от того, каким путём получена выборка (кэш или фолбэк).
        """
        result = self._get("/v1/user/cache_token_list", {"id": address.lower()})
        if not isinstance(result, list):
            return []
        return [t for t in result if isinstance(t, dict) and _is_core_token(t)]

    def get_tokens(self, address: str) -> list:
        """Все core-токены кошелька по всем ненулевым сетям (свежая выборка).

        Формат токена совместим с DeBank: ``id``/``chain``/``symbol``/
        ``amount``/``price`` (нативные — ``id`` == ключ сети, не hex).

        Схема: ``total_balance`` → ``cache_token_list`` (один запрос на все
        сети); при сбое кэша — фолбэк на по-сетевой ``token_list``. Пустой
        ``chain_list`` (пустой кошелёк) → запросы токенов не выполняются,
        как поступает и расширение.
        """
        total = self._fetch_total_balance(address)
        chain_list = total.get("chain_list", [])
        if not isinstance(chain_list, list):
            return []

        chains = [
            c.get("id") for c in chain_list
            if isinstance(c, dict) and c.get("id")
            and (c.get("usd_value") or 0) > 0
        ]
        if not chains:
            return []

        try:
            return self.get_cache_token_list(address)
        except Exception:
            pass  # фолбэк: по-сетевой token_list (is_all=false → только core)

        tokens: list = []
        for chain_id in chains:
            result = self._get(
                "/v1/user/token_list",
                {"id": address.lower(), "chain_id": chain_id, "is_all": "false"},
            )
            if isinstance(result, list):
                tokens.extend(t for t in result if isinstance(t, dict) and _is_core_token(t))
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
        """Список core-токенов одной сети (один запрос).

        Защищённый эндпоинт: не должен вызываться серийно (анти-бот);
        основной путь — ``get_cache_token_list``.
        """
        result = self._get(
            "/v1/user/token_list",
            {"id": address.lower(), "chain_id": chain_id, "is_all": "false"},
        )
        if not isinstance(result, list):
            return []
        return [t for t in result if isinstance(t, dict) and _is_core_token(t)]
