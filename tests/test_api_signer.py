# tests/test_api_signer.py
"""Подпись DeBank/Rabby: воспроизведение реальных подписей из HAR байт-в-байт."""
from __future__ import annotations
import re
import time

from app.integrations.api_signer import (
    DEBANK_SIGN_PREFIX,
    RABBY_SIGN_PREFIX,
    generate_nonce,
    sign_request,
)

# Реальные запросы веб-версии Rabby (0.94.1) из HAR-записи.
RABBY_HAR_CASES = [
    {
        "path": "/v1/user/total_balance",
        "params": {"id": "0x9f5dc2f69006fffae20247a95f1dfa0cb057bce9", "is_core": "true"},
        "nonce": "n_0ryzNnvSRFkqUUbQRpMB8B02QBeVcbPoetlbgb3i",
        "ts": 1786263519,
        "expected": "43052aa374a600b60115d582eb7f050a5503e565f8751b93957aae5909df6cb3",
    },
    {
        "path": "/v1/user/used_chain_list",
        "params": {"id": "0x9f5dc2f69006fffae20247a95f1dfa0cb057bce9"},
        "nonce": "n_EVweBnesOpliKLFvILfZMDKThTQ7rTtSN5xdvgul",
        "ts": 1786263518,
        "expected": "d3738f0e51cab2aa96a5a79c57f799b53bb129645f975d2bf30646a9c690e596",
    },
    {
        "path": "/v1/user/complex_app_list",
        "params": {"id": "0x9f5dc2f69006fffae20247a95f1dfa0cb057bce9"},
        "nonce": "n_zo801t45Hd2u6TRBVTCG3er0BPEdA3d7nVRT60v0",
        "ts": 1786263518,
        "expected": "b21c4e4457cc8bf8137c312cd19e69f70216ccdde781fb0057ba4d2ef341697d",
    },
    {
        "path": "/v1/user/collection_list",
        "params": {"id": "0x9f5dc2f69006fffae20247a95f1dfa0cb057bce9", "is_all": "true"},
        "nonce": "n_BiUqeFXTTrNJa6IU55Wdy7C5NGb0cIHxeVinCAgC",
        "ts": 1786263527,
        "expected": "ce90130dbfe44dea6e317ee7588133397076e32207e30e9e0283aea112459edb",
    },
]


def test_rabby_signature_matches_har_byte_for_byte():
    for case in RABBY_HAR_CASES:
        sign = sign_request(
            case["params"], "GET", case["path"],
            prefix=RABBY_SIGN_PREFIX, nonce=case["nonce"], ts=case["ts"],
        )
        assert sign["signature"] == case["expected"], case["path"]
        assert sign["nonce"] == case["nonce"]
        assert sign["ts"] == case["ts"]
        assert sign["version"] == "v2"


def test_param_order_does_not_change_signature():
    a = sign_request({"b": "2", "a": "1"}, "GET", "/x",
                     prefix=RABBY_SIGN_PREFIX, nonce="n_test", ts=1000)
    b = sign_request({"a": "1", "b": "2"}, "GET", "/x",
                     prefix=RABBY_SIGN_PREFIX, nonce="n_test", ts=1000)
    assert a["signature"] == b["signature"]


def test_prefix_changes_signature():
    rabby = sign_request({"id": "0xabc"}, "GET", "/x",
                         prefix=RABBY_SIGN_PREFIX, nonce="n_test", ts=1000)
    debank = sign_request({"id": "0xabc"}, "GET", "/x",
                          prefix=DEBANK_SIGN_PREFIX, nonce="n_test", ts=1000)
    assert rabby["signature"] != debank["signature"]


def test_default_prefix_is_debank():
    explicit = sign_request({}, "GET", "/x",
                            prefix=DEBANK_SIGN_PREFIX, nonce="n_test", ts=1000)
    default = sign_request({}, "GET", "/x", nonce="n_test", ts=1000)
    assert default["signature"] == explicit["signature"]


def test_generate_nonce_format():
    for _ in range(20):
        assert re.fullmatch(r"n_[0-9A-Za-z]{40}", generate_nonce())


def test_autogenerates_nonce_and_ts():
    before = int(time.time())
    sign = sign_request({}, "GET", "/x", prefix=RABBY_SIGN_PREFIX)
    assert re.fullmatch(r"n_[0-9A-Za-z]{40}", sign["nonce"])
    assert before <= sign["ts"] <= before + 2
