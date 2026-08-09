from __future__ import annotations
import json
import threading
import time
import uuid

import curl_cffi.requests as cffi_requests

from app.integrations.api_signer import sign_request

API_BASE = "https://api.debank.com"


class DeBankClient:
    """Клиент DeBank API. Прокси обязателен."""

    _api_key: str = "3b92c003-ddc1-4c2d-b36e-781838f362c5"
    _api_key_lock: threading.Lock = threading.Lock()
    REQUEST_TIMEOUT: float = 3.0

    def __init__(self, proxy: str, impersonate: str = "chrome124") -> None:
        if not proxy:
            raise ValueError("Прокси обязателен для DeBank API")
        self._init_ts = int(time.time())
        self._random_at = self._init_ts
        self._random_id = uuid.uuid4().hex
        self._session = cffi_requests.Session(
            impersonate=impersonate,
            proxies={"https": proxy, "http": proxy},
        )

    def _build_headers(self, params: dict, method: str, path: str) -> dict:
        with DeBankClient._api_key_lock:
            api_key = DeBankClient._api_key
        sign = sign_request(params, method, path)
        account = json.dumps(
            {"random_at": self._random_at, "random_id": self._random_id,
             "user_addr": None, "connected_addr": None},
            separators=(",", ":"),
        )
        return {
            "Referer": "https://debank.com/",
            "Origin": "https://debank.com",
            "X-API-Key": api_key,
            "X-API-Time": str(self._init_ts),
            "x-api-ts": str(sign["ts"]),
            "x-api-nonce": sign["nonce"],
            "x-api-ver": sign["version"],
            "x-api-sign": sign["signature"],
            "source": "web",
            "account": account,
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
            with DeBankClient._api_key_lock:
                DeBankClient._api_key = new_key
        data = resp.json()
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return data

    def get_tokens(self, address: str) -> list:
        result = self._get("/token/cache_balance_list", {"user_addr": address})
        return result if isinstance(result, list) else []

    def get_total_usd(self, address: str) -> float:
        result = self._get("/asset/total_net_curve", {"user_addr": address, "days": 1})
        points = result.get("usd_value_list", []) if isinstance(result, dict) else []
        if not points:
            return 0.0

        last_point = points[-1]
        if not isinstance(last_point, (list, tuple)) or len(last_point) < 2:
            return 0.0

        try:
            return float(last_point[1] or 0.0)
        except (TypeError, ValueError):
            return 0.0
