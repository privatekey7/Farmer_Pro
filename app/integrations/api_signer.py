# app/integrations/api_signer.py
from __future__ import annotations
import hashlib
import hmac as hmac_lib
import random
import time
from typing import TypedDict

"""Подпись запросов к Rabby API (HMAC-SHA256).

Проверено воспроизведением подписи из HAR клиента Rabby байт-в-байт
(tests/test_api_signer.py):

    K    = sha256("rabby-api\\n{nonce}\\n{ts}")
    M    = sha256("{METHOD}\\n{path}\\n{отсортированные по ключу query-параметры}")
    sign = HMAC-SHA256(key=K, msg=M)
"""

NONCE_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXTZabcdefghiklmnopqrstuvwxyz"
NONCE_LENGTH = 40

RABBY_SIGN_PREFIX = "rabby-api"


class SignResult(TypedDict):
    signature: str
    nonce: str
    ts: int
    version: str


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
    prefix: str = RABBY_SIGN_PREFIX,
    nonce: str | None = None,
    ts: int | None = None,
    version: str = "v2",
) -> SignResult:
    """Подпись запроса. ``nonce``/``ts`` передаются явно только в тестах."""
    ts = ts or int(time.time())
    nonce = nonce or generate_nonce()
    sorted_p = sort_params(params)
    key = sha256_hex(f"{prefix}\n{nonce}\n{ts}")
    msg = sha256_hex(f"{method.upper()}\n{path}\n{sorted_p}")
    signature = hmac_sha256(key, msg)
    return {"signature": signature, "nonce": nonce, "ts": ts, "version": version}
