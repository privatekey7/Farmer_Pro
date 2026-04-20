from __future__ import annotations
import hashlib
import hmac as hmac_lib
import json
import random
import threading
import time
import uuid

import curl_cffi.requests as cffi_requests

API_BASE = "https://api.debank.com"
NONCE_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXTZabcdefghiklmnopqrstuvwxyz"
NONCE_LENGTH = 40


def sort_params(params: dict) -> str:
    if not params:
        return ""
    return "&".join(f"{k}={v}" for k, v in sorted(params.items()))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hmac_sha256(key_str: str, msg_str: str) -> str:
    return hmac_lib.new(
        key_str.encode("utf-8"),
        msg_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_nonce() -> str:
    return "n_" + "".join(random.choices(NONCE_ALPHABET, k=NONCE_LENGTH))


def sign_request(
    params: dict,
    method: str,
    path: str,
    nonce: str | None = None,
    ts: int | None = None,
    version: str = "v2",
) -> dict:
    ts = ts or int(time.time())
    nonce = nonce or generate_nonce()
    prefix = "debank-web\n" if version == "v2.1" else "debank-api\n"
    sorted_p = sort_params(params)
    K = sha256_hex(f"{prefix}{nonce}\n{ts}")
    M = sha256_hex(f"{method.upper()}\n{path}\n{sorted_p}")
    signature = hmac_sha256(K, M)
    return {"signature": signature, "nonce": nonce, "ts": ts, "version": version}


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
