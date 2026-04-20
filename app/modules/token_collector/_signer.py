# app/modules/token_collector/_signer.py
from __future__ import annotations
import logging
from typing import Any

from eth_account import Account
from web3 import Web3

logger = logging.getLogger(__name__)

# BIP44 деривационный путь для Ethereum
BIP44_PATH = "m/44'/60'/0'/0/0"


class TransactionReverted(RuntimeError):
    """Транзакция отправлена и подтверждена, но reverted (status=0)."""
    def __init__(self, tx_hash: str) -> None:
        super().__init__(tx_hash)
        self.tx_hash = tx_hash


class InsufficientFundsError(ValueError):
    """Нода отклонила транзакцию: недостаточно ETH для gas * price + value."""

_ERC20_ABI = [
    {
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
        "stateMutability": "view",
    },
    {
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
        "stateMutability": "nonpayable",
    },
]
_MAX_UINT256 = 2 ** 256 - 1


def ensure_erc20_approval(
    w3: Web3,
    token_address: str,
    owner: str,
    spender: str,
    amount: int,
    private_key: str,
) -> bool:
    """
    Проверяет allowance ERC-20 и при необходимости отправляет approve(spender, MAX_UINT256).
    Возвращает True если allowance достаточен или approve прошёл успешно.
    """
    token = w3.eth.contract(
        address=Web3.to_checksum_address(token_address),
        abi=_ERC20_ABI,
    )
    allowance = token.functions.allowance(
        Web3.to_checksum_address(owner),
        Web3.to_checksum_address(spender),
    ).call()

    if allowance >= amount:
        return True

    logger.info("Approving %s → spender %s...", token_address[:10], spender[:10])

    nonce = w3.eth.get_transaction_count(Web3.to_checksum_address(owner), "pending")
    try:
        base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
        tx = token.functions.approve(
            Web3.to_checksum_address(spender), _MAX_UINT256
        ).build_transaction({
            "from": Web3.to_checksum_address(owner),
            "nonce": nonce,
            "maxFeePerGas": base_fee * 11 // 10,
            "maxPriorityFeePerGas": 1,  # минимум 1 wei — некоторые RPC отклоняют 0
        })
    except Exception:
        tx = token.functions.approve(
            Web3.to_checksum_address(spender), _MAX_UINT256
        ).build_transaction({
            "from": Web3.to_checksum_address(owner),
            "nonce": nonce,
            "gasPrice": w3.eth.gas_price * 2,
        })

    signed = w3.eth.account.sign_transaction(tx, private_key)
    try:
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    except Exception as e:
        if "insufficient funds" in str(e).lower():
            raise InsufficientFundsError(str(e))
        raise

    try:
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    except Exception as e:
        logger.warning("Approve receipt timeout for %s: %s", tx_hash.hex(), e)
        return True  # tx отправлена — считаем что одобрено, swap попробует сам

    if receipt.status == 0:
        logger.error("Approve reverted: %s", tx_hash.hex())
        return False

    logger.info("Approve confirmed: %s", tx_hash.hex())
    return True


def derive_address(wallet_raw: str, wallet_type: str) -> tuple[str, str]:
    """
    Возвращает (address, private_key_hex).
    wallet_type: "private_key" | "mnemonic"
    Бросает ValueError для неизвестного типа.
    """
    if wallet_type == "private_key":
        acct = Account.from_key(wallet_raw)
        return acct.address, wallet_raw
    elif wallet_type == "mnemonic":
        Account.enable_unaudited_hdwallet_features()
        acct = Account.from_mnemonic(wallet_raw, account_path=BIP44_PATH)
        return acct.address, acct.key.hex()
    else:
        raise ValueError(f"Неизвестный тип кошелька: {wallet_type}")


def sign_and_send(
    w3: Web3,
    tx_req: dict,
    private_key: str,
    address: str,
) -> tuple[str, Any] | tuple[str, None]:
    """
    Подписывает и отправляет транзакцию. Возвращает (tx_hash_hex, receipt).
    Если receipt.status == 0 (reverted) — логирует и возвращает (tx_hash_hex, None).
    """
    nonce = w3.eth.get_transaction_count(address, "pending")

    def _parse(v) -> int:
        """Парсит hex (0x-префикс) или decimal строку/int в int."""
        if isinstance(v, str):
            return int(v, 16) if v.startswith("0x") else int(v)
        return int(v)

    # EIP-1559 (если есть maxFeePerGas)
    if "maxFeePerGas" in tx_req:
        tx = {
            "from": address,
            "to": Web3.to_checksum_address(tx_req["to"]),
            "data": tx_req.get("data", "0x"),
            "value": _parse(tx_req["value"]),
            "gas": _parse(tx_req.get("gasLimit") or tx_req.get("gas")),
            "maxFeePerGas": _parse(tx_req["maxFeePerGas"]),
            "maxPriorityFeePerGas": _parse(tx_req["maxPriorityFeePerGas"]),
            "nonce": nonce,
            "chainId": _parse(tx_req["chainId"]),
        }
        # Берём актуальный baseFee из сети и ставим maxFeePerGas = baseFee×2 + priority.
        # На L2 (OP, ARB, Base) LI.FI выставляет priority=0 — это нормально, sequencer не требует чаевых.
        # НЕ форсируем минимум 1 gwei: 0 or 1e9 завышает газ в 100–1000x на L2.
        try:
            base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
            # max(1, ...) — некоторые RPC (publicnode/OP и др.) отклоняют tip=0
            priority = max(1, tx["maxPriorityFeePerGas"])
            tx["maxPriorityFeePerGas"] = priority
            tx["maxFeePerGas"] = base_fee * 11 // 10 + priority
            logger.info("maxFeePerGas set to %d (baseFee=%d priority=%d)", tx["maxFeePerGas"], base_fee, priority)
        except Exception:
            tx["maxFeePerGas"] = int(tx["maxFeePerGas"] * 2)
    else:
        # Legacy
        tx = {
            "from": address,
            "to": Web3.to_checksum_address(tx_req["to"]),
            "data": tx_req.get("data", "0x"),
            "value": _parse(tx_req["value"]),
            "gas": _parse(tx_req.get("gasLimit") or tx_req.get("gas")),
            "gasPrice": _parse(tx_req["gasPrice"]),
            "nonce": nonce,
            "chainId": _parse(tx_req["chainId"]),
        }
        # Бампаем gasPrice на случай роста baseFee
        try:
            base_fee = w3.eth.get_block("latest").get("baseFeePerGas") or 0
            if base_fee:
                tx["gasPrice"] = max(tx["gasPrice"], base_fee * 2)
            else:
                tx["gasPrice"] = int(tx["gasPrice"] * 2)
        except Exception:
            tx["gasPrice"] = int(tx["gasPrice"] * 2)

    for _attempt in range(3):
        signed = w3.eth.account.sign_transaction(tx, private_key)
        try:
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            break
        except Exception as e:
            e_str = str(e)
            if "nonce too low" in e_str and _attempt < 2:
                tx["nonce"] = w3.eth.get_transaction_count(address, "latest")
                logger.warning("Nonce too low, retrying with nonce=%d (attempt %d)", tx["nonce"], _attempt + 2)
                continue
            if "insufficient funds" in e_str.lower():
                raise InsufficientFundsError(e_str)
            raise
    tx_hash_hex = "0x" + tx_hash.hex()

    try:
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    except Exception as e:
        logger.warning("Receipt fetch failed for %s (%s), will rely on bridge poller", tx_hash_hex, e)
        return tx_hash_hex, None

    if receipt.status == 0:
        logger.error("Транзакция reverted: %s", tx_hash_hex)
        raise TransactionReverted(tx_hash_hex)

    return tx_hash_hex, receipt
