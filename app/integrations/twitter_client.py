from __future__ import annotations
import secrets
from dataclasses import dataclass
from enum import Enum

import curl_cffi.requests as cffi_requests

REQUEST_TIMEOUT: float = 10.0
BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "=1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
_VERIFY_URL = "https://x.com/i/api/1.1/account/multi/list.json"


class TwitterTokenStatus(str, Enum):
    OK        = "ok"
    INVALID   = "invalid"
    SUSPENDED = "suspended"
    LOCKED    = "locked"
    ERROR     = "error"


@dataclass
class TokenCheckResult:
    status: TwitterTokenStatus
    username: str | None = None


class TwitterClient:
    """Stateless sync Twitter API client. One instance per request."""

    def __init__(self, proxy: str) -> None:
        csrf = secrets.token_hex(16)
        self._session = cffi_requests.Session(impersonate="chrome124")
        self._session.proxies.update({"http": proxy, "https": proxy})
        self._session.headers.update({
            "authorization": f"Bearer {BEARER_TOKEN}",
            "x-csrf-token": csrf,
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en",
            "referer": "https://x.com/",
            "origin": "https://x.com",
        })
        self._session.cookies.update({"ct0": csrf})

    def check_token(self, auth_token: str) -> TokenCheckResult:
        """Check a single auth_token. Never raises — returns ERROR on all failures."""
        try:
            self._session.cookies.update({"auth_token": auth_token})
            resp = self._session.get(_VERIFY_URL, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return self._parse_ok(resp)
            return self._map_error(resp)
        except Exception:
            return TokenCheckResult(status=TwitterTokenStatus.ERROR)

    @staticmethod
    def _parse_ok(resp) -> TokenCheckResult:
        try:
            users = resp.json().get("users", [])
            if not users:
                return TokenCheckResult(status=TwitterTokenStatus.ERROR)
            user = users[0]
            if user.get("is_suspended"):
                return TokenCheckResult(status=TwitterTokenStatus.SUSPENDED)
            return TokenCheckResult(
                status=TwitterTokenStatus.OK,
                username=user.get("screen_name"),
            )
        except Exception:
            return TokenCheckResult(status=TwitterTokenStatus.ERROR)

    @staticmethod
    def _map_error(resp) -> TokenCheckResult:
        try:
            code = resp.json()["errors"][0]["code"]
        except Exception:
            return TokenCheckResult(status=TwitterTokenStatus.ERROR)

        mapping = {
            32:  TwitterTokenStatus.INVALID,
            64:  TwitterTokenStatus.SUSPENDED,
            326: TwitterTokenStatus.LOCKED,
        }
        status = mapping.get(code, TwitterTokenStatus.ERROR)
        return TokenCheckResult(status=status)
