from __future__ import annotations
from pathlib import Path
from urllib.parse import urlparse
from app.core.models import ProxyConfig
from app.core.exceptions import ParseError


def parse_lines(filepath: str) -> list[str]:
    """Читает файл, удаляет пустые строки и комментарии (#)."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {filepath}")
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def parse_proxies(filepath: str) -> list[ProxyConfig]:
    """Парсит файл с прокси в произвольном формате."""
    return [_parse_proxy_line(line) for line in parse_lines(filepath)
            if _parse_proxy_line(line) is not None]


def _parse_proxy_line(line: str) -> ProxyConfig | None:
    """
    Алгоритм (приоритет сверху вниз):
    1. Содержит :// → формат с протоколом
    2. Содержит @ (без ://) → user:pass@host:port
    3. Разбить по : → 2 части = host:port, 4 части = host:port:user:pass
    """
    try:
        if "://" in line:
            parsed = urlparse(line)
            protocol = parsed.scheme or "http"
            host = parsed.hostname or ""
            port = parsed.port or 8080
            user = parsed.username
            password = parsed.password
            return ProxyConfig(host=host, port=int(port), user=user, password=password, protocol=protocol)

        if "@" in line:
            credentials, hostport = line.rsplit("@", 1)
            user, password = credentials.split(":", 1)
            host, port_str = hostport.rsplit(":", 1)
            return ProxyConfig(host=host, port=int(port_str), user=user, password=password)

        parts = line.split(":")
        if len(parts) == 2:
            return ProxyConfig(host=parts[0], port=int(parts[1]))
        if len(parts) == 4:
            return ProxyConfig(host=parts[0], port=int(parts[1]), user=parts[2], password=parts[3])

    except (ValueError, AttributeError):
        pass
    return None


def parse_wallets(filepath: str) -> list[dict]:
    """
    Определяет тип каждого кошелька:
    - private_key: 0x + 64 hex символа
    - mnemonic: 12 или 24 слова
    - address: 0x + 40 hex символа
    """
    result = []
    for line in parse_lines(filepath):
        result.append({"raw": line, "type": _detect_wallet_type(line)})
    return result


def _detect_wallet_type(value: str) -> str:
    import re
    if re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
        return "private_key"
    if re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
        return "address"
    word_count = len(value.split())
    if word_count in (12, 24):
        return "mnemonic"
    return "unknown"
