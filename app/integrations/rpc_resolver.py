# app/integrations/rpc_resolver.py
from __future__ import annotations
import logging

from web3 import Web3

logger = logging.getLogger(__name__)

# Publicnode RPCs — стабильные публичные ноды, идут первыми в списке кандидатов.
# Обновляй при добавлении новых цепей на https://publicnode.com
_PUBLICNODE_RPCS: dict[int, str] = {
    1:       "https://ethereum-rpc.publicnode.com",
    10:      "https://optimism-rpc.publicnode.com",
    25:      "https://cronos-evm-rpc.publicnode.com",
    56:      "https://bsc-rpc.publicnode.com",
    100:     "https://gnosis-rpc.publicnode.com",
    130:     "https://unichain-rpc.publicnode.com",
    137:     "https://polygon-bor-rpc.publicnode.com",
    146:     "https://sonic-rpc.publicnode.com:443",
    204:     "https://opbnb-rpc.publicnode.com",
    252:     "https://fraxtal-rpc.publicnode.com:443",
    369:     "https://pulsechain-rpc.publicnode.com",
    1088:    "https://metis-rpc.publicnode.com:443",
    1284:    "https://moonbeam-rpc.publicnode.com",
    1868:    "https://soneium-rpc.publicnode.com",
    2222:    "https://kava-evm-rpc.publicnode.com",
    5000:    "https://mantle-rpc.publicnode.com",
    8453:    "https://base-rpc.publicnode.com",
    42161:   "https://arbitrum-one-rpc.publicnode.com",
    42220:   "https://celo-rpc.publicnode.com",
    43114:   "https://avalanche-c-chain-rpc.publicnode.com",
    59144:   "https://linea-rpc.publicnode.com",
    81457:   "https://blast-rpc.publicnode.com",
    167000:  "https://taiko-rpc.publicnode.com",
    534352:  "https://scroll-rpc.publicnode.com",
}


class RpcResolver:
    """
    Получает рабочий Web3 для заданного chain_id.
    Приоритет: Publicnode → LI.FI URLs → Relay URL → ChainList URLs.
    Кэширует последний рабочий RPC. Если он перестал отвечать —
    вызови rotate(chain_id) чтобы перейти на следующий.
    """

    def __init__(
        self,
        lifi_rpcs: dict[int, list[str]],
        relay_rpcs: dict[int, str],
        chainlist_rpcs: dict[int, list[str]] | None = None,
    ) -> None:
        # Собираем кандидатов: Publicnode → LI.FI → Relay → ChainList
        all_ids = set(lifi_rpcs) | set(relay_rpcs) | set(chainlist_rpcs or {}) | set(_PUBLICNODE_RPCS)
        self._candidates: dict[int, list[str]] = {}
        for cid in all_ids:
            urls: list[str] = []
            # Publicnode — высший приоритет
            if cid in _PUBLICNODE_RPCS:
                urls.append(_PUBLICNODE_RPCS[cid])
            urls.extend(lifi_rpcs.get(cid, []))
            if cid in relay_rpcs:
                urls.append(relay_rpcs[cid])
            urls.extend((chainlist_rpcs or {}).get(cid, []))
            # Нормализуем схему и убираем дубли
            seen: set[str] = set()
            normed: list[str] = []
            for u in urls:
                u = u if u.startswith("http") else f"https://{u}"
                if u not in seen:
                    seen.add(u)
                    normed.append(u)
            self._candidates[cid] = normed

        self._cache: dict[int, Web3] = {}
        self._failed: set[str] = set()  # плохие URL в рамках сессии

    # ------------------------------------------------------------------

    def get_web3(self, chain_id: int) -> Web3:
        """Возвращает Web3, пробуя следующий кандидат если кэш пуст."""
        if chain_id in self._cache:
            return self._cache[chain_id]
        return self._probe(chain_id)

    def rotate(self, chain_id: int) -> Web3:
        """
        Помечает текущий RPC как нерабочий и возвращает Web3 со следующим.
        Вызывать при ошибках соединения (connection refused, timeout и т.п.).
        """
        if chain_id in self._cache:
            w3 = self._cache.pop(chain_id)
            bad = str(w3.provider.endpoint_uri)  # type: ignore[union-attr]
            self._failed.add(bad)
            logger.warning("[RpcResolver] Marked bad RPC for chain %s: %s", chain_id, bad)
        return self._probe(chain_id)

    # ------------------------------------------------------------------

    def _probe(self, chain_id: int) -> Web3:
        candidates = self._candidates.get(chain_id, [])
        if not candidates:
            raise RuntimeError(f"No RPC candidates for chain {chain_id}")

        for url in candidates:
            if url in self._failed:
                continue
            try:
                w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 10}))
                if w3.is_connected():
                    logger.info("[RpcResolver] Connected chain %s via %s", chain_id, url)
                    self._cache[chain_id] = w3
                    return w3
                else:
                    self._failed.add(url)
            except Exception as e:
                logger.debug("[RpcResolver] %s failed: %s", url, e)
                self._failed.add(url)

        raise RuntimeError(
            f"All {len(candidates)} RPCs failed for chain {chain_id}"
        )
