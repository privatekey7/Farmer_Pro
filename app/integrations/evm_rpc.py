from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
from web3 import Web3
from app.core.retry import retry


class EvmRpcClient:
    """Обёртка над web3.py для async-вызовов через run_in_executor."""

    def __init__(self, rpc_url: str, executor: ThreadPoolExecutor | None = None) -> None:
        self._w3 = Web3(Web3.HTTPProvider(rpc_url))
        self._executor = executor

    async def get_balance(self, address: str) -> int:
        """Возвращает нативный баланс в wei."""
        loop = asyncio.get_event_loop()
        checksum = Web3.to_checksum_address(address)
        return await loop.run_in_executor(
            self._executor,
            self._w3.eth.get_balance,
            checksum,
        )

    def is_connected(self) -> bool:
        return self._w3.is_connected()
