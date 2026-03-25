# app/integrations/lifi_client.py
from __future__ import annotations
import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

LIFI_API_KEY = "aeaa4f26-c3c3-4b71-aad3-50bd82faf815.1e83cb78-2d75-412d-a310-57272fd0e622"

# DeBank key → EVM chain ID. Единственная хардкоженная константа маппинга.
DEBANK_TO_CHAIN_ID: dict[str, int] = {
    # ── Поддерживаются LI.FI ──────────────────────────────────────────
    "eth":         1,
    "bsc":         56,
    "matic":       137,
    "avax":        43114,
    "op":          10,
    "arb":         42161,
    "base":        8453,
    "era":         324,
    "linea":       59144,
    "scrl":        534352,
    "blast":       81457,
    "mnt":         5000,
    "mode":        34443,
    "frax":        252,
    "opbnb":       204,
    "taiko":       167000,
    "abs":         2741,
    "ink":         57073,
    "soneium":     1868,
    "uni":         130,
    "world":       480,
    "morph":       2818,
    "swell":       1923,
    "bob":         60808,
    "sophon":      50104,
    "xdai":        100,
    "celo":        42220,
    "cro":         25,
    "boba":        288,
    "metis":       1088,
    "mobm":        1284,
    "fuse":        122,
    "klay":        8217,
    "rsk":         30,
    "tlos":        40,
    "flr":         14,
    "ron":         2020,
    "sei":         1329,
    "gravity":     1625,
    "lisk":        1135,
    "ape":         33139,
    "ethlink":     42793,
    "sonic":       146,
    "corn":        21000000,
    "bera":        80094,
    "lens":        232,
    "hyper":       999,
    "hemi":        43111,
    "plume":       98866,
    "katana":      747474,
    "plasma":      9745,
    "monad":       143,
    "stable":      988,
    "megaeth":     4326,
    "itze":        13371,
    # ── НЕ поддерживаются LI.FI (auto-skip через supported_chain_ids) ─
    "ftm":         250,
    "movr":        1285,
    "iotx":        4689,
    "dfk":         53935,
    "nova":        42170,
    "doge":        2000,
    "kava":        2222,
    "cfx":         1030,
    "core":        1116,
    "wemix":       1111,
    "oas":         248,
    "zora":        7777777,
    "manta":       169,
    "zeta":        7000,
    "merlin":      4200,
    "xlayer":      196,
    "btr":         200901,
    "b2":          223,
    "croze":       388,
    "zircuit":     48900,
    "hsk":         177,
    "story":       1514,
    "cyber":       7560,
    "chiliz":      88888,
    "orderly":     291,
    "rari":        1380012617,
    "reya":        1729,
    "bb":          6001,
    "goat":        2345,
    "tac":         239,
    "botanix":     3637,
}

# Обратный маппинг: chain_id → DeBank key
LIFI_CHAIN_TO_DEBANK: dict[int, str] = {v: k for k, v in DEBANK_TO_CHAIN_ID.items()}


# ── Исключения ─────────────────────────────────────────────────────────────

class LiFiError(Exception):
    """Базовое исключение LI.FI клиента."""


class LiFiNoRouteError(LiFiError):
    """Нет доступных маршрутов для свапа/бриджа."""


class LiFiApiError(LiFiError):
    """HTTP-ошибка LI.FI API."""
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code


class LiFiTimeoutError(LiFiError):
    """Таймаут запроса к LI.FI."""


@dataclass
class LiFiChainRegistry:
    """Результат инициализации LI.FI /chains. Передаётся в модуль как единый объект."""
    supported_ids: set[int]
    rpc_by_id: dict[int, list[str]]       # ВСЕ rpcUrls на сеть (для fallback)
    native_token_by_id: dict[int, dict]
    lifi_key_by_id: dict[int, str]        # LI.FI key ("bas", "opt", "pol"...)
    name_by_id: dict[int, str]            # человекочитаемое название для логов

    def resolve(self, debank_key: str) -> int | None:
        """DeBank key → chain ID, None если неизвестна или не поддерживается LI.FI."""
        chain_id = DEBANK_TO_CHAIN_ID.get(debank_key)
        if chain_id is None or chain_id not in self.supported_ids:
            return None
        return chain_id


class LiFiClient:
    """HTTP-клиент LI.FI API. Sync, использовать через run_in_executor."""

    BASE_URL = "https://li.quest/v1"
    INTEGRATOR = "RetroFarm"
    FEE = 0.005
    RETRY_ATTEMPTS = 3
    RETRY_DELAY = 3  # секунды

    def __init__(self, proxy: str | None = None) -> None:
        headers = {
            "x-lifi-api-key": LIFI_API_KEY,
            "Content-Type": "application/json",
        }
        self._session = httpx.Client(
            headers=headers,
            proxy=proxy,
            timeout=30.0,
        )

    def _get(self, path: str, params: dict | None = None) -> dict:
        """GET запрос с retry (3 попытки, задержка 3с)."""
        url = self.BASE_URL + path
        last_exc: Exception | None = None
        for attempt in range(self.RETRY_ATTEMPTS):
            try:
                resp = self._session.get(url, params=params or {})
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    last_exc = e
                    if attempt < self.RETRY_ATTEMPTS - 1:
                        time.sleep(self.RETRY_DELAY)
                    continue
                # HTTP 400 с "No available quotes" → специальное исключение
                try:
                    body = e.response.json()
                    msg = body.get("message", "")
                except Exception:
                    msg = str(e)
                if "No available quotes" in msg:
                    raise LiFiNoRouteError(msg) from e
                raise LiFiApiError(e.response.status_code, msg) from e
            except httpx.TimeoutException as e:
                raise LiFiTimeoutError(str(e)) from e
        raise LiFiApiError(429, "Rate limit after retries") from last_exc

    def get_chains(self, chain_types: str = "EVM") -> list[dict]:
        """GET /chains — список сетей с RPC URLs и native token info."""
        data = self._get("/chains", {"chainTypes": chain_types})
        return data.get("chains", [])

    def get_tools(self) -> dict:
        """GET /tools — доступные мосты и обменники."""
        return self._get("/tools")

    def get_connections(self, from_chain: int, to_chain: int, chain_types: str = "EVM") -> list[dict]:
        """GET /connections — проверка существования маршрута."""
        data = self._get("/connections", {
            "fromChain": from_chain,
            "toChain": to_chain,
            "chainTypes": chain_types,
        })
        return data.get("connections", [])

    def get_gas_prices(self) -> dict:
        """GET /gas/prices — текущие цены газа по всем сетям (в wei)."""
        return self._get("/gas/prices")

    def get_gas_suggestion(self, chain_id: int) -> dict:
        """GET /gas/suggestion/{chain_id} — рекомендуемый объём нативного токена."""
        return self._get(f"/gas/suggestion/{chain_id}")

    def get_quote(
        self,
        from_chain: int,
        to_chain: int,
        from_token: str,
        to_token: str,
        from_amount: int,
        from_address: str,
        to_address: str | None = None,
        slippage: float = 0.005,
    ) -> dict:
        """GET /quote — котировка свапа или бриджа."""
        params = {
            "fromChain": from_chain,
            "toChain": to_chain,
            "fromToken": from_token,
            "toToken": to_token,
            "fromAmount": str(from_amount),
            "fromAddress": from_address,
            "toAddress": to_address or from_address,
            "integrator": self.INTEGRATOR,
            "fee": self.FEE,
            "slippage": slippage,
        }
        return self._get("/quote", params)

    def get_status(self, tx_hash: str, bridge: str, from_chain: int, to_chain: int) -> dict:
        """GET /status — статус кросс-чейн трансфера.
        LI.FI возвращает 404 пока tx не проиндексирована — нормируем в NOT_FOUND."""
        try:
            return self._get("/status", {
                "txHash": tx_hash,
                "bridge": bridge,
                "fromChain": from_chain,
                "toChain": to_chain,
            })
        except LiFiApiError as e:
            if e.status_code == 404:
                return {"status": "NOT_FOUND"}
            raise
