from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ResultStatus(Enum):
    OK = "ok"
    ERROR = "error"
    SKIP = "skip"


@dataclass
class ProxyConfig:
    host: str
    port: int
    user: str | None = None
    password: str | None = None
    protocol: str = "http"  # "http" | "socks5"

    def to_url(self) -> str:
        """Возвращает прокси URL для httpx."""
        if self.user and self.password:
            return f"{self.protocol}://{self.user}:{self.password}@{self.host}:{self.port}"
        return f"{self.protocol}://{self.host}:{self.port}"


@dataclass
class RunContext:
    items: list[str]
    proxies: list[ProxyConfig]
    rpc_urls: list[str]
    concurrency: int
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Result:
    item: str
    status: ResultStatus
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
