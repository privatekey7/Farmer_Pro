# app/integrations/chainlist_client.py
from __future__ import annotations
import logging
import re
import requests

logger = logging.getLogger(__name__)

# Источники в порядке приоритета: первый успешный используется
_SOURCES = [
    "https://chainlist.org/rpcs.json",
    "https://chainid.network/chains.json",
    "https://chainid.network/chains_mini.json",
]

# Шаблоны RPC которые требуют API-ключей или не являются публичными HTTP
_SKIP_PATTERNS = re.compile(
    r"\$\{|wss://|ws://|localhost|127\.0\.0\.1|0\.0\.0\.0",
    re.IGNORECASE,
)


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
}


def fetch_chainlist_rpcs(
    timeout: int = 10,
    max_per_chain: int = 5,
) -> dict[int, list[str]]:
    """
    Загружает публичные HTTP RPC для всех EVM-цепей с chainlist.org.
    Возвращает {chain_id: [url, ...]}.
    Не бросает исключений — при любой ошибке возвращает пустой dict.
    """
    data = None
    for url in _SOURCES:
        try:
            resp = requests.get(url, timeout=timeout, headers=_HEADERS)
            resp.raise_for_status()
            data = resp.json()
            logger.debug("ChainList loaded from %s", url)
            break
        except Exception as e:
            logger.debug("ChainList source %s failed: %s", url, e)

    if data is None:
        logger.warning("ChainList: all sources failed, continuing without extra RPCs")
        return {}

    result: dict[int, list[str]] = {}
    try:
        for entry in data:
            chain_id = entry.get("chainId")
            if not isinstance(chain_id, int):
                continue
            rpcs = entry.get("rpc", [])
            good: list[str] = []
            for rpc in rpcs:
                # rpc может быть строкой или dict {"url": ..., "tracking": ...}
                url = rpc if isinstance(rpc, str) else rpc.get("url", "")
                if not url:
                    continue
                if _SKIP_PATTERNS.search(url):
                    continue
                if not url.startswith("http"):
                    continue
                good.append(url)
                if len(good) >= max_per_chain:
                    break
            if good:
                result[chain_id] = good
    except Exception as e:
        logger.warning("ChainList parse error: %s", e)
        return {}

    logger.info("ChainList: loaded RPCs for %d chains", len(result))
    return result
