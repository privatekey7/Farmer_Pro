# app/modules/token_collector/_signer.py
from __future__ import annotations
import logging
import time
from types import SimpleNamespace
from typing import Any

from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError, TransactionNotFound

from app.core.logger import SUCCESS

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
# Потолок maxFeePerGas = baseFee × PCT / 100 + priority (EIP-1559).
# Это ВЕРХНИЙ предел: платится фактический baseFee, разница не списывается.
# baseFee растёт до 12.5% за блок, поэтому ×1.1 не переживает и одного
# заполненного блока. Свопы и approve — ×2, но только если хватает баланса:
# иначе потолок опускается до того, что баланс покрывает (см. _fit_max_fee).
SWAP_MAX_FEE_PCT = 200
# Минимальный рабочий потолок: по нему проверяется «хватит ли газа», и с ним
# уходит «весь баланс минус газ» (бридж, refuel).
MIN_MAX_FEE_PCT = 110
SEND_ALL_MAX_FEE_PCT = MIN_MAX_FEE_PCT

# Ожидание подтверждения. web3.wait_for_transaction_receipt опрашивает RPC
# каждые 0.1 с — publicnode в ответ отдаёт 403, и транзакция считалась
# «pending»: пропускались и успешные свопы, и откаты. Опрос раз в 2 с,
# сбои RPC (403, таймауты) ожидание не прерывают.
RECEIPT_TIMEOUT_SEC = 150
RECEIPT_POLL_SEC = 2.0


def _fallback_receipt(chain_id: int, tx_hex: str, proxy: str | None = None) -> Any | None:
    """Receipt через ноды встроенного реестра RPC (app.integrations.onchain).

    publicnode на eth_getTransactionReceipt стабильно отвечает 403 — не от
    частоты запросов, а всегда: каждая tx ждала полные 150 с и оставалась
    «PENDING», хотя давно прошла. Тесты подменяют эту функцию.
    """
    from app.integrations import onchain

    r = onchain.call(int(chain_id), "eth_getTransactionReceipt", [tx_hex], proxy)
    if not isinstance(r, dict):
        return None
    return SimpleNamespace(status=int(r.get("status") or "0x1", 16),
                           blockNumber=int(r.get("blockNumber") or "0x0", 16))


def _wait_receipt(w3: Web3, tx_hash: Any, chain_id: int | None = None,
                  timeout: float | None = None, poll: float | None = None) -> Any | None:
    """Receipt транзакции или None, если за timeout она не попала в блок.

    Если основной RPC отвечает ошибкой (403 и т.п.), дальше статус спрашивается
    у других нод реестра (chain_id обязателен для этого).
    """
    timeout = RECEIPT_TIMEOUT_SEC if timeout is None else timeout
    poll = RECEIPT_POLL_SEC if poll is None else poll
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    use_fallback = False
    while True:
        try:
            if use_fallback:
                receipt = _fallback_receipt(chain_id, _hex(tx_hash), getattr(w3, "farmer_proxy", None))
            else:
                receipt = w3.eth.get_transaction_receipt(tx_hash)
            if receipt is not None:
                return receipt
        except TransactionNotFound:
            pass
        except Exception as e:  # 403 / таймаут RPC
            last_error = e
            if not use_fallback and chain_id:
                use_fallback = True
                logger.info("Receipt RPC error (%s) — polling other RPC nodes", str(e)[:80])
                continue
        if time.monotonic() >= deadline:
            if last_error is not None:
                logger.warning("Receipt polling errors for %s: %s", _hex(tx_hash), last_error)
            return None
        time.sleep(poll)


def _hex(tx_hash: Any) -> str:
    h = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
    return h if h.startswith("0x") else "0x" + h


def _priority_fee(w3: Web3, fallback: int = 1) -> int:
    """Чаевые по подсказке сети (eth_maxPriorityFeePerGas).

    Раньше approve уходил с чаевыми 1 wei: на Ethereum валидаторы такие не
    включают, approve висел, своп получал тот же nonce и вытеснял его — и
    откатывался без allowance.
    """
    try:
        return max(1, int(w3.eth.max_priority_fee))
    except Exception:
        return max(1, fallback)


def _is_connection_error(err: Exception) -> bool:
    """Обрыв соединения / таймаут, а не ответ ноды (tx могла уйти, а могла и нет)."""
    s = str(err).lower()
    return any(k in s for k in (
        "connection", "reset", "aborted", "timed out", "timeout", "remote end closed", "10054", "max retries",
    ))


def _rebroadcast(chain_id: int, raw_hex: str, tx_hex: str, proxy: str | None) -> bool:
    """Та же подписанная tx — через другие ноды. True — tx в сети.

    Безопасно: тот же nonce и тот же хэш, второй раз деньги не уйдут — нода
    ответит «already known» / «nonce too low», если tx уже принята.
    """
    from app.integrations import onchain

    try:
        if onchain.call(int(chain_id), "eth_getTransactionByHash", [tx_hex], proxy):
            return True
    except Exception:
        pass
    try:
        onchain.call(int(chain_id), "eth_sendRawTransaction", [raw_hex], proxy)
        return True
    except Exception as e:
        s = str(e).lower()
        return "already known" in s or "known transaction" in s or "nonce too low" in s


def _to_int(v: Any) -> int:
    """hex (0x-префикс) / decimal строка / int → int."""
    if not v:
        return 0
    if isinstance(v, str):
        return int(v, 16) if v.startswith("0x") else int(v)
    return int(v)


def get_allowance(w3: Web3, token_address: str, owner: str, spender: str) -> int:
    token = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=_ERC20_ABI)
    return int(token.functions.allowance(
        Web3.to_checksum_address(owner), Web3.to_checksum_address(spender),
    ).call())


def simulate_tx(w3: Web3, tx_req: dict, address: str) -> bool | None:
    """Симуляция транзакции (eth_estimateGas) без отправки и без затрат.

    True — пройдёт; False — откатится (honeypot / токен нельзя продать /
    маршрут сломан); None — симуляция не удалась по другой причине (сбой RPC),
    решать вызывающему.
    """
    try:
        w3.eth.estimate_gas({
            "from": Web3.to_checksum_address(address),
            "to": Web3.to_checksum_address(tx_req["to"]),
            "data": tx_req.get("data") or "0x",
            "value": _to_int(tx_req.get("value")),
        })
        return True
    except ContractLogicError:
        # Любой откат исполнения, в т.ч. пользовательская ошибка контракта:
        # web3 бросает ContractCustomError('0xf4059071') — слова «revert» в
        # тексте нет (так откатывается своп USA на bsc).
        return False
    except Exception as e:
        return False if "revert" in str(e).lower() else None


def effective_priority(w3: Web3, quote_tip: int) -> int:
    """Чаевые валидатору: меньшее из котировки и подсказки сети, минимум 1 wei.

    Relay на zkSync ставит в котировке 1 gwei при baseFee 0.045 gwei и
    подсказке сети 0 — запас на газ и «ожидаемая комиссия» раздувались в ~22
    раза, и правило выгоды отказывало бриджу с era ($1 оставался на месте).
    """
    try:
        suggestion = int(w3.eth.max_priority_fee)
    except Exception:
        return max(1, int(quote_tip))
    return max(1, min(int(quote_tip), suggestion))


def _l1_fee(w3: Web3, tx_req: dict) -> int:
    from app.modules.token_collector._bridge_logic import _get_l1_fee_safe
    return _get_l1_fee_safe(w3, tx_req)


def _fit_max_fee(w3: Web3, address: str, gas: int, value: int, max_fee: int,
                 base_fee: int, priority: int, l1_fee: int) -> int:
    """Опускает maxFeePerGas, если value + gas × maxFee + L1 не покрывается балансом.

    Нода отклоняет tx, если balance < value + gas × maxFee (+ L1 на OP-stack).
    Потолок ×2 нужен против застревания, но не должен блокировать отправку,
    когда баланса хватает только на меньший потолок. Ниже baseFee + priority
    не опускаем — такая tx не попадёт в текущий блок.
    """
    if gas <= 0:
        return max_fee
    try:
        balance = w3.eth.get_balance(address)
    except Exception:
        return max_fee
    available = balance - value - l1_fee
    if gas * max_fee <= available:
        return max_fee
    fitted = available // gas if available > 0 else 0
    return max(base_fee + priority, min(max_fee, fitted))


def _send_approve(w3: Web3, token: Any, owner: str, spender: str, value: int, private_key: str,
                  ops: list[dict] | None = None, chain: str = "", label: str = "") -> bool:
    """Отправляет approve(spender, value) и ждёт подтверждения. True — одобрено.

    Не подтвердился за RECEIPT_TIMEOUT_SEC → False: своп без allowance гарантированно
    откатится (и сожжёт газ), поэтому его не отправляем.
    """
    owner_cs = Web3.to_checksum_address(owner)
    spender_cs = Web3.to_checksum_address(spender)
    nonce = w3.eth.get_transaction_count(owner_cs, "pending")
    try:
        base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
        priority = _priority_fee(w3)
        tx = token.functions.approve(spender_cs, value).build_transaction({
            "from": owner_cs,
            "nonce": nonce,
            "maxFeePerGas": base_fee * SWAP_MAX_FEE_PCT // 100 + priority,
            "maxPriorityFeePerGas": priority,
        })
        tx["maxFeePerGas"] = _fit_max_fee(
            w3, owner_cs, int(tx.get("gas") or 0), 0, tx["maxFeePerGas"], base_fee, priority,
            _l1_fee(w3, {"data": tx.get("data") or "0x"}),
        )
    except Exception:
        tx = token.functions.approve(spender_cs, value).build_transaction({
            "from": owner_cs,
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

    op = {"type": "approve", "chain": chain, "detail": label, "usd": 0.0, "tx": _hex(tx_hash), "status": "PENDING"}
    if ops is not None:
        ops.append(op)

    receipt = _wait_receipt(w3, tx_hash, chain_id=tx.get("chainId"))
    if receipt is None:
        logger.warning("Approve %s not confirmed in %ds — swap skipped", _hex(tx_hash), RECEIPT_TIMEOUT_SEC)
        return False

    if receipt.status == 0:
        op["status"] = "REVERTED"
        logger.error("Approve reverted: %s", _hex(tx_hash))
        return False

    op["status"] = "CONFIRMED"
    logger.log(SUCCESS, "Approve confirmed: %s", _hex(tx_hash))
    return True


def ensure_erc20_approval(
    w3: Web3,
    token_address: str,
    owner: str,
    spender: str,
    amount: int,
    private_key: str,
    ops: list[dict] | None = None,
    chain: str = "",
    label: str = "",
) -> bool:
    """
    Проверяет allowance ERC-20 и при необходимости даёт approve РОВНО на amount.
    Возвращает True если allowance достаточен или approve подтверждён.
    ops (если передан) пополняется записями об отправленных approve.

    Бесконечный approve (MAX_UINT256) оставался на кошельках навсегда: при
    взломе контракта-спендера (у LI.FI так было в 07.2024) токены можно вывести.
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

    logger.info("Approving %s → spender %s (exact amount %d)...", token_address[:10], spender[:10], amount)

    if allowance > 0:
        # USDT-подобные токены отклоняют смену ненулевого allowance на другой
        # ненулевой — сначала обнуляем (бывает, если старый approve был меньше).
        if not _send_approve(w3, token, owner, spender, 0, private_key, ops, chain, label):
            return False
    return _send_approve(w3, token, owner, spender, amount, private_key, ops, chain, label)


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
    max_fee_pct: int = SWAP_MAX_FEE_PCT,
    keep_fees: bool = False,
) -> tuple[str, Any] | tuple[str, None]:
    """
    Подписывает и отправляет транзакцию. Возвращает (tx_hash_hex, receipt).
    receipt=None — не попала в блок за RECEIPT_TIMEOUT_SEC; status == 0 → TransactionReverted.

    max_fee_pct — потолок maxFeePerGas от свежего baseFee (см. SWAP_MAX_FEE_PCT /
    SEND_ALL_MAX_FEE_PCT); потолок опускается под баланс (_fit_max_fee), чтобы
    рост baseFee между проверкой и отправкой не превращался в отказ ноды.
    keep_fees=True — комиссии из tx_req уже посчитаны вызывающим под точную
    сумму value и не пересчитываются.
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
        # Берём актуальный baseFee из сети: maxFeePerGas = baseFee × max_fee_pct/100 + priority.
        # На L2 (OP, ARB, Base) LI.FI выставляет priority=0 — это нормально, sequencer не требует чаевых.
        # НЕ форсируем минимум 1 gwei: 0 or 1e9 завышает газ в 100–1000x на L2.
        if not keep_fees:
            try:
                base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
                # max(1, ...) — некоторые RPC (publicnode/OP и др.) отклоняют tip=0
                priority = effective_priority(w3, tx["maxPriorityFeePerGas"])
                tx["maxPriorityFeePerGas"] = priority
                tx["maxFeePerGas"] = _fit_max_fee(
                    w3, address, tx["gas"], tx["value"],
                    base_fee * max_fee_pct // 100 + priority, base_fee, priority,
                    _l1_fee(w3, tx_req),
                )
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
        if not keep_fees:
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
            if _is_connection_error(e):
                # Соединение оборвалось при отправке — tx могла уйти. Повторяем ТУ ЖЕ
                # подписанную tx через другие ноды (не новую: иначе двойная отправка).
                tx_hex = _hex(signed.hash)
                logger.warning("Connection dropped while sending %s (%s) — rebroadcasting the same "
                               "signed tx via other RPC nodes", tx_hex, str(e)[:80])
                if _rebroadcast(tx["chainId"], _hex(signed.raw_transaction), tx_hex,
                                getattr(w3, "farmer_proxy", None)):
                    tx_hash = signed.hash
                    break
            raise
    tx_hash_hex = _hex(tx_hash)

    receipt = _wait_receipt(w3, tx_hash, chain_id=tx["chainId"])
    if receipt is None:
        logger.warning("Tx %s not confirmed in %ds", tx_hash_hex, RECEIPT_TIMEOUT_SEC)
        return tx_hash_hex, None

    if receipt.status == 0:
        logger.error("Транзакция reverted: %s", tx_hash_hex)
        raise TransactionReverted(tx_hash_hex)

    return tx_hash_hex, receipt
