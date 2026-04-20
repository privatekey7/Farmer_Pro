from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

import curl_cffi.requests as cffi_requests

REQUEST_TIMEOUT: float = 10.0
_ME_URL = "https://discord.com/api/v9/users/@me"


class DiscordTokenStatus(str, Enum):
    OK       = "ok"
    INVALID  = "invalid"
    DISABLED = "disabled"
    ERROR    = "error"


@dataclass
class DiscordTokenCheckResult:
    status: DiscordTokenStatus
    username: str | None = None
    user_id: str | None = None
    email: str | None = None
    has_phone: bool = False


class DiscordClient:
    """Stateless sync Discord API client. One instance per token check."""

    def __init__(self, proxy: str | None = None) -> None:
        self._session = cffi_requests.Session(impersonate="chrome124")
        if proxy:
            self._session.proxies.update({"http": proxy, "https": proxy})
        self._session.headers.update({
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "referer": "https://discord.com/channels/@me",
            "origin": "https://discord.com",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "x-discord-locale": "en-US",
            "x-discord-timezone": "Etc/UTC",
        })

    def check_token(self, token: str) -> DiscordTokenCheckResult:
        """Check a single Discord token. Never raises — returns ERROR on all failures."""
        try:
            self._session.headers.update({"authorization": token})
            resp = self._session.get(_ME_URL, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return self._parse_ok(resp)
            return self._map_error(resp)
        except Exception:
            return DiscordTokenCheckResult(status=DiscordTokenStatus.ERROR)

    @staticmethod
    def _parse_ok(resp) -> DiscordTokenCheckResult:
        try:
            data = resp.json()
            return DiscordTokenCheckResult(
                status=DiscordTokenStatus.OK,
                username=data.get("username"),
                user_id=data.get("id"),
                email=data.get("email"),
                has_phone=bool(data.get("phone")),
            )
        except Exception:
            return DiscordTokenCheckResult(status=DiscordTokenStatus.ERROR)

    @staticmethod
    def _map_error(resp) -> DiscordTokenCheckResult:
        if resp.status_code == 401:
            return DiscordTokenCheckResult(status=DiscordTokenStatus.INVALID)
        if resp.status_code == 403:
            return DiscordTokenCheckResult(status=DiscordTokenStatus.DISABLED)
        return DiscordTokenCheckResult(status=DiscordTokenStatus.ERROR)
