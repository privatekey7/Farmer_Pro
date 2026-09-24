# app/integrations/rabby_client.py
from __future__ import annotations
import threading
import time
from typing import Any

from app.integrations import http_pool
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

REQUEST_TIMEOUT = 8.0

# Общий на процесс магазин ключа: клиент создаётся на каждую выборку, но при
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

    Отдаёт ответы эндпоинтов как есть. ВНИМАНИЕ: с 11.09.2026 total_balance,
    complex_app_list и token_list для части адресов возвращают ЧУЖИЕ данные —
    итог из них не берётся, проверка — в ``balance_verifier``.

    Заголовки идентификации должны ТОЧНО повторять клиент Rabby (сверено с
    HAR браузерного расширения): подписные заголовки — в нижнем регистре,
    ``x-api-time`` — время выдачи ключа, поверх impersonate-фингерпринта
    досылаются браузерные заголовки. Анти-бот API на любое отклонение
    отвечает фейковым 429 с пустым телом при верной подписи (разбор инцидента
    — docs/incident-429-antibot.md в DeBankChecker).

    HTTP идёт через общий пул keep-alive сессий (``http_pool``): клиент
    дешёвый, создаётся на каждую выборку. 429/5xx не повторяются здесь —
    выборку повторяет верификатор уже через другой прокси.
    """

    def __init__(self, proxy: str) -> None:
        if not proxy:
            raise ValueError("Прокси обязателен для Rabby API")
        self._proxy = proxy
        self._api_key, self._key_time = _current_key()

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

    def _get(self, path: str, params: dict | None = None) -> Any:
        params = params or {}
        headers = self._build_headers(params, "GET", path)
        resp = http_pool.get(
            API_BASE + path,
            self._proxy,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        # Ротация ключа читается ДО raise_for_status: сервер может выдать
        # новый ключ и вместе с ошибкой (429/403) — раньше он терялся.
        new_key = resp.headers.get("x-set-api-key")
        if new_key and new_key != self._api_key:
            self._key_time = _rotate_key(new_key)
            self._api_key = new_key

        resp.raise_for_status()

        data = resp.json()
        if isinstance(data, dict) and "data" in data and set(data.keys()) <= {"data", "error_code"}:
            return data["data"]
        return data

    def get_total_balance(self, address: str) -> dict:
        """Агрегат: ``{total_usd_value, chain_list: [{id, usd_value}]}``.

        НЕ источник итога (бывает заражён чужими суммами) — только контроль и
        подсказка, в каких сетях кэш токенов мог устареть.
        ``is_core=true`` отсекает скам/непроверенные токены — как галка в UI Rabby.
        """
        result = self._get("/v1/user/total_balance", {"id": address.lower(), "is_core": "true"})
        return result if isinstance(result, dict) else {}

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

    def get_token_list(self, address: str, chain_id: str) -> list:
        """Свежий список core-токенов одной сети (один запрос).

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

    def get_complex_app_list(self, address: str) -> list:
        """DeFi-протоколы с позициями: ``portfolio_item_list`` со ``stats``/
        ``asset_token_list``/``detail`` в формате DeBank."""
        result = self._get("/v1/user/complex_app_list", {"id": address.lower()})
        if isinstance(result, dict):
            apps = result.get("apps", [])
            return apps if isinstance(apps, list) else []
        return result if isinstance(result, list) else []

    def get_chain_list(self) -> list:
        """Сети Rabby: ``id`` (строковый), ``community_id`` (EVM chain id), ``native_token_id``."""
        result = self._get("/v1/chain/list")
        return result if isinstance(result, list) else []
