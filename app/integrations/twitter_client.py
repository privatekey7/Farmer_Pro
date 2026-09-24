from __future__ import annotations
import secrets
import threading
from dataclasses import dataclass
from enum import Enum

import curl_cffi.requests as cffi_requests

REQUEST_TIMEOUT: float = 10.0
BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "=1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
_VERIFY_URL = "https://x.com/i/api/1.1/account/multi/list.json"

# Кэш сессий на поток и прокси. curl_cffi Session НЕ потокобезопасна, поэтому
# у каждого рабочего потока — своя сессия на прокси. Переиспользование сессии
# держит TLS-соединение к x.com живым между токенами (без повторного
# handshake на каждый токен).
_thread_local = threading.local()


def _session_for(proxy: str) -> tuple[cffi_requests.Session, str]:
    """Сессия (и её csrf) для прокси в рамках текущего потока."""
    cache = getattr(_thread_local, "sessions", None)
    if cache is None:
        cache = {}
        _thread_local.sessions = cache
    entry = cache.get(proxy)
    if entry is None:
        csrf = secrets.token_hex(16)
        session = cffi_requests.Session(impersonate="chrome124")
        session.proxies.update({"http": proxy, "https": proxy})
        session.headers.update({
            "authorization": f"Bearer {BEARER_TOKEN}",
            "x-csrf-token": csrf,
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en",
            "referer": "https://x.com/",
            "origin": "https://x.com",
        })
        if len(cache) > 64:            # защита от неограниченного роста
            cache.clear()
        entry = cache[proxy] = (session, csrf)
    return entry


def _is_proxy_dead(err: Exception) -> bool:
    """Сбой на уровне прокси/соединения (не ответ Twitter) — стоит сменить IP."""
    s = str(err).lower()
    return any(k in s for k in (
        "connect tunnel failed", "407", "proxy", "timed out", "timeout",
        "connection", "reset", "could not resolve",
    ))


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
    # True — ошибка прокси/сети (а не ответ Twitter): повтор с другого IP.
    proxy_dead: bool = False


class TwitterClient:
    """Stateless sync Twitter API client. Сессия переиспользуется на прокси."""

    def __init__(self, proxy: str) -> None:
        self._session, self._csrf = _session_for(proxy)

    def check_token(self, auth_token: str) -> TokenCheckResult:
        """Check a single auth_token. Never raises — returns ERROR on all failures."""
        try:
            # csrf и auth_token переустанавливаем на каждый запрос: сессия
            # общая для разных токенов, а x.com может ротировать ct0 в ответе.
            self._session.cookies.update({"ct0": self._csrf, "auth_token": auth_token})
            resp = self._session.get(_VERIFY_URL, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return self._parse_ok(resp)
            return self._map_error(resp)
        except Exception as e:
            return TokenCheckResult(status=TwitterTokenStatus.ERROR, proxy_dead=_is_proxy_dead(e))

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
