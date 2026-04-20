# app/modules/token_collector/_collector_logic.py
from __future__ import annotations
import asyncio
import logging
import random
import threading
import time
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


def _get_native_addr(
    chain_id: int,
    native_token_by_id: dict[int, dict],
    relay_native_by_id: dict[int, dict],
) -> str:
    """Возвращает адрес нативного токена сети. Приоритет: LI.FI → Relay → ""."""
    addr = (
        native_token_by_id.get(chain_id, {}).get("address")
        or relay_native_by_id.get(chain_id, {}).get("address")
        or ""
    )
    return addr


def _resolve_contract(token: dict) -> str:
    """
    Возвращает адрес контракта токена.
    DeBank API не возвращает поле contract_address — контракт ERC-20 хранится в поле id.
    Для нативных токенов id = ключ цепи (не hex), поэтому проверяем startswith("0x").
    """
    token_id = token.get("id", "")
    explicit = token.get("contract_address", "")
    if explicit:
        return explicit.lower()
    # Если id выглядит как hex-адрес — это контракт ERC-20
    if token_id.startswith("0x"):
        return token_id.lower()
    return ""


def _is_native_token(
    token: dict,
    chain_id: int,
    native_token_by_id: dict[int, dict],
    relay_native_by_id: dict[int, dict],
    debank_key: str = "",
) -> bool:
    """Определяет является ли токен нативным для данной сети."""
    token_id = token.get("id", "")
    contract = _resolve_contract(token)
    zero_addr = "0x0000000000000000000000000000000000000000"

    # Условие 1: id совпадает с DeBank chain key (нативный — "eth", "op", "arb"...)
    if debank_key and token_id == debank_key:
        return True

    # Условие 2: пустой или нулевой адрес контракта — нативный токен
    if not contract or contract == zero_addr:
        return True

    # Условие 3: адрес из registry (для Celo, RARI и др.)
    native_addr = _get_native_addr(chain_id, native_token_by_id, relay_native_by_id)
    if native_addr and contract == native_addr.lower():
        return True

    return False


async def fetch_and_swap(
    wallet: dict,                               # {"raw": "0x...", "type": "private_key"}
    lifi_client: Any,                           # LiFiClient
    debank_client: Any,                         # DeBankClient
    rpc_resolver: Any,                          # RpcResolver
    settings: Any,                              # CollectorSettings
    native_token_by_id: dict[int, dict],
    relay_native_by_id: dict[int, dict],
    lifi_chain_ids: set[int],
    supported_chain_ids: set[int],
    stop_event: threading.Event,
    target_chain_ids: set[int] | None = None,   # исключаем таргет-цепи из total_usd
) -> dict:
    """
    ШАГ 1-2: получить балансы DeBank, отфильтровать,
    своп не-нативных токенов в нативный через LI.FI.
    Возвращает статистику: chains_processed, chains_skipped, tokens_swapped, total_usd.
    """
    from app.integrations.lifi_client import DEBANK_TO_CHAIN_ID, LiFiNoRouteError
    from app.modules.token_collector._signer import derive_address, sign_and_send, ensure_erc20_approval, InsufficientFundsError

    address, private_key = derive_address(wallet["raw"], wallet["type"])
    loop = asyncio.get_running_loop()

    # ШАГ 1: Получить балансы
    DEBANK_RETRY = 10
    tokens: list[dict] = []
    last_exc: Exception | None = None
    for _ in range(DEBANK_RETRY):
        if stop_event.is_set():
            return {}
        try:
            tokens = await loop.run_in_executor(None, debank_client.get_tokens, address)
            break
        except Exception as e:
            last_exc = e
            await asyncio.sleep(3)
    else:
        logger.error("[%s] DeBank failed after %d retries: %s", address[:10], DEBANK_RETRY, last_exc)
        return {}

    # Группируем по chain
    chains: dict[str, list[dict]] = {}
    for t in tokens:
        chains.setdefault(t.get("chain", ""), []).append(t)

    chains_processed: list[str] = []
    chains_skipped: list[str] = []
    tokens_swapped = 0
    total_usd = 0.0
    gasless_map: dict[int, dict] = {}  # chain_id → {chain_id, debank_key, eth_balance, max_gas_needed_wei, native_token_addr, tokens}

    all_chain_keys = sorted(chains.keys())
    logger.info(
        "[Wallet %s] Fetched %d tokens across %d chains (%s)",
        address[:10], len(tokens), len(all_chain_keys), ", ".join(all_chain_keys)
    )

    for debank_key, chain_tokens in chains.items():
        if stop_event.is_set():
            break

        # Фильтр: исключённые сети
        if debank_key.lower() in (s.lower() for s in settings.excluded_chains):
            chains_skipped.append(f"{debank_key} (excluded)")
            logger.info("[Wallet %s] Skipping %s: excluded by user", address[:10], debank_key)
            continue

        # Фильтр: нет в маппинге
        chain_id = DEBANK_TO_CHAIN_ID.get(debank_key)
        if chain_id is None:
            chains_skipped.append(f"{debank_key} (no mapping)")
            continue

        # Фильтр: нет в supported_chain_ids
        if chain_id not in supported_chain_ids:
            chains_skipped.append(f"{debank_key} (not supported)")
            logger.info("[Wallet %s] Skipping %s: chain not supported by LI.FI", address[:10], debank_key)
            continue

        # Определяем нативный адрес для этой сети
        native_addr = _get_native_addr(chain_id, native_token_by_id, relay_native_by_id)
        if native_addr:
            native_token_addr = native_addr
        else:
            native_token_addr = "0x0000000000000000000000000000000000000000"

        # ШАГ 2: своп не-нативных токенов
        chain_native_usd = 0.0
        chain_skipped_dust = 0
        chain_swapped = 0

        for token in chain_tokens:
            if stop_event.is_set():
                break

            symbol = token.get("symbol", "?")
            value_usd = token.get("price", 0) * token.get("amount", 0)

            if value_usd < settings.min_token_usd:
                logger.info(
                    "[Wallet %s] [%s] Skip %s: $%.4f < min $%.2f",
                    address[:10], debank_key, symbol, value_usd, settings.min_token_usd,
                )
                chain_skipped_dust += 1
                continue

            if _is_native_token(token, chain_id, native_token_by_id, relay_native_by_id, debank_key):
                logger.info(
                    "[Wallet %s] [%s] Native %s $%.2f — no swap needed",
                    address[:10], debank_key, symbol, value_usd,
                )
                chain_native_usd += value_usd
                # Нативный ETH на source-цепях не считаем как "собранный" здесь —
                # он будет учтён как sent_usd при успешном бридже.
                continue

            contract = _resolve_contract(token)
            decimals = token.get("decimals", 18)

            # Получаем w3 и реальный on-chain баланс — DeBank-кэш может быть устаревшим
            try:
                w3 = rpc_resolver.get_web3(chain_id)
            except Exception as e:
                logger.warning("[Wallet %s] [%s] No RPC for chain %d, skipping %s: %s",
                               address[:10], debank_key, chain_id, symbol, e)
                continue

            _ERC20_BALANCE_ABI = [{"inputs": [{"name": "account", "type": "address"}],
                                   "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
                                   "type": "function", "stateMutability": "view"}]

            async def _get_balance(w3_inst) -> int:
                tc = w3_inst.eth.contract(
                    address=w3_inst.to_checksum_address(contract), abi=_ERC20_BALANCE_ABI
                )
                return await loop.run_in_executor(
                    None, tc.functions.balanceOf(w3_inst.to_checksum_address(address)).call
                )

            try:
                from_amount = await _get_balance(w3)
            except Exception as e:
                # Пробуем следующий RPC
                try:
                    w3 = rpc_resolver.rotate(chain_id)
                    from_amount = await _get_balance(w3)
                except Exception as e2:
                    logger.warning("[Wallet %s] [%s] balanceOf failed for %s, using DeBank amount: %s",
                                   address[:10], debank_key, symbol, e2)
                    amount = token.get("amount", 0)
                    from_amount = int(Decimal(str(amount)) * Decimal(10 ** decimals))

            if from_amount == 0:
                logger.info("[Wallet %s] [%s] Skip %s: on-chain balance is 0", address[:10], debank_key, symbol)
                continue

            logger.info("[Wallet %s] [%s] Swapping %s ($%.2f) → native", address[:10], debank_key, symbol, value_usd)

            try:
                # Проверяем маршрут
                connections = await loop.run_in_executor(
                    None, lifi_client.get_connections, chain_id, chain_id
                )
                if not connections:
                    logger.info("[Wallet %s] [%s] No swap route for %s on %s", address[:10], debank_key, symbol, debank_key)
                    continue

                # Получаем котировку
                quote = await loop.run_in_executor(
                    None,
                    lifi_client.get_quote,
                    chain_id, chain_id,
                    contract, native_token_addr,
                    from_amount, address,
                    address, settings.slippage,
                )

                tx_req = quote["transactionRequest"]

                # Проверяем что ETH хватит на газ свапа (sign_and_send удваивает gasPrice)
                def _hi(v) -> int:
                    if not v:
                        return 0
                    if isinstance(v, str):
                        return int(v, 16) if v.startswith("0x") else int(v)
                    return int(v)

                gas_limit = _hi(tx_req.get("gasLimit") or tx_req.get("gas"))
                # Зеркалируем логику sign_and_send: для EIP-1559 используем priority из котировки.
                # На L2 (OP, ARB, Base) LI.FI выставляет priority=0 — это нормально.
                # НЕ форсируем 1 gwei: это завышает оценку газа в 100–1000x на L2.
                if "maxFeePerGas" in tx_req:
                    quote_priority = _hi(tx_req.get("maxPriorityFeePerGas"))
                    try:
                        base_fee = await loop.run_in_executor(
                            None, lambda: w3.eth.get_block("latest")["baseFeePerGas"]
                        )
                        # Зеркалируем sign_and_send: maxFeePerGas = baseFee * 1.1 + priority
                        effective_gas_price = base_fee * 11 // 10 + quote_priority
                    except Exception:
                        effective_gas_price = _hi(tx_req.get("maxFeePerGas"))
                else:
                    # Legacy tx (BSC и другие non-EIP-1559 сети)
                    quote_gas_price = _hi(tx_req.get("gasPrice"))
                    try:
                        base_fee = await loop.run_in_executor(
                            None, lambda: w3.eth.get_block("latest").get("baseFeePerGas") or 0
                        )
                        effective_gas_price = max(quote_gas_price, base_fee) if base_fee else quote_gas_price
                    except Exception:
                        effective_gas_price = quote_gas_price

                tx_value = _hi(tx_req.get("value"))
                # +100_000 gas — резерв на ERC-20 approve (выполняется перед swap)
                APPROVE_GAS_BUFFER = 100_000
                eth_needed = tx_value + (gas_limit + APPROVE_GAS_BUFFER) * effective_gas_price
                eth_balance = await loop.run_in_executor(None, w3.eth.get_balance, address)

                # +10% запас на флуктуацию baseFee между предпроверкой и отправкой
                if eth_balance < int(eth_needed * 1.1):
                    logger.warning(
                        "[Wallet %s] [%s] Skip %s swap: ETH balance %d < needed %d "
                        "(gas_limit=%d gas_price=%d approve_buf=%d value=%d)",
                        address[:10], debank_key, symbol,
                        eth_balance, eth_needed,
                        gas_limit, effective_gas_price,
                        APPROVE_GAS_BUFFER * effective_gas_price,
                        tx_value,
                    )
                    # Запоминаем для возможного рефьюела
                    if chain_id not in gasless_map:
                        gasless_map[chain_id] = {
                            "chain_id": chain_id,
                            "debank_key": debank_key,
                            "eth_balance": eth_balance,
                            "max_gas_needed_wei": 0,
                            "native_token_addr": native_token_addr,
                            "tokens": [],
                        }
                    gc = gasless_map[chain_id]
                    gc["max_gas_needed_wei"] = max(gc["max_gas_needed_wei"], int(eth_needed * 1.1))
                    gc["tokens"].append({"contract": contract, "symbol": symbol, "value_usd": value_usd})
                    continue

                # ERC-20 approve: даём разрешение LI.FI diamond тратить токен
                spender = tx_req.get("to", "")
                if spender:
                    approved = await loop.run_in_executor(
                        None, ensure_erc20_approval,
                        w3, contract, address, spender, from_amount, private_key,
                    )
                    if not approved:
                        logger.error(
                            "[Wallet %s] [%s] Approve failed for %s, skipping swap",
                            address[:10], debank_key, symbol,
                        )
                        continue

                tx_hash, receipt = await loop.run_in_executor(
                    None, sign_and_send, w3, tx_req, private_key, address
                )

                if receipt is None:
                    # Таймаут ожидания — транзакция может быть pending, не reverted
                    logger.warning(
                        "[Wallet %s] [%s] Swap tx pending (receipt timeout): %s",
                        address[:10], debank_key, tx_hash,
                    )
                    continue

                logger.info(
                    "[Wallet %s] [%s] Swap %s → native | tx: %s | confirmed in block %s",
                    address[:10], debank_key, symbol, tx_hash, receipt.blockNumber
                )
                tokens_swapped += 1
                chain_swapped += 1
                total_usd += value_usd

                # Задержка после каждого свапа
                delay = random.randint(settings.delay_min, settings.delay_max)
                logger.info("[Wallet %s] [%s] Delay %ds after swap", address[:10], debank_key, delay)
                for _ in range(delay):
                    if stop_event.is_set():
                        break
                    await asyncio.sleep(1)

            except InsufficientFundsError:
                logger.warning(
                    "[Wallet %s] [%s] Skip %s swap: insufficient ETH for gas (node rejected)",
                    address[:10], debank_key, symbol,
                )
            except LiFiNoRouteError as e:
                logger.info("[Wallet %s] [%s] No route for %s: %s", address[:10], debank_key, symbol, e)
            except Exception as e:
                logger.error("[Wallet %s] [%s] Swap error for %s: %s", address[:10], debank_key, symbol, e)

        logger.info(
            "[Wallet %s] [%s] Chain summary: native=$%.2f swapped=%d dust_skipped=%d",
            address[:10], debank_key, chain_native_usd, chain_swapped, chain_skipped_dust,
        )
        chains_processed.append(debank_key)

    return {
        "address": address,
        "private_key": private_key,
        "chains_processed": ", ".join(chains_processed),
        "chains_skipped": ", ".join(chains_skipped),
        "tokens_swapped": tokens_swapped,
        "total_collected_usd": round(total_usd, 2),
        "gasless_chains": list(gasless_map.values()),
    }


async def retry_gasless_swaps(
    gasless_chains: list[dict],
    address: str,
    private_key: str,
    lifi_client: Any,
    rpc_resolver: Any,
    settings: Any,
    native_token_by_id: dict[int, dict],
    relay_native_by_id: dict[int, dict],
    lifi_chain_ids: set[int],
    stop_event: threading.Event,
) -> dict:
    """
    ШАГ 2.6: повторный своп токенов на цепях, которые были рефьюелены.
    Не вызывает DeBank — использует список токенов из gasless_chains.
    Возвращает {chains_processed, tokens_swapped, total_usd}.
    """
    from app.integrations.lifi_client import DEBANK_TO_CHAIN_ID, LiFiNoRouteError
    from app.modules.token_collector._signer import sign_and_send, ensure_erc20_approval, InsufficientFundsError

    loop = asyncio.get_running_loop()
    chains_processed: list[str] = []
    tokens_swapped = 0
    total_usd = 0.0

    _ERC20_BALANCE_ABI = [{"inputs": [{"name": "account", "type": "address"}],
                           "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
                           "type": "function", "stateMutability": "view"}]

    for gc in gasless_chains:
        if stop_event.is_set():
            break

        chain_id: int = gc["chain_id"]
        debank_key: str = gc["debank_key"]
        native_token_addr: str = gc["native_token_addr"]

        if chain_id not in lifi_chain_ids:
            continue

        try:
            w3 = rpc_resolver.get_web3(chain_id)
        except Exception as e:
            logger.warning("[Retry] No RPC for chain %s: %s", chain_id, e)
            continue

        chain_swapped = 0

        for token_info in gc.get("tokens", []):
            if stop_event.is_set():
                break

            contract: str = token_info["contract"]
            symbol: str = token_info["symbol"]
            value_usd: float = token_info["value_usd"]

            if value_usd < settings.min_token_usd:
                continue

            # Актуальный on-chain баланс
            try:
                tc = w3.eth.contract(
                    address=w3.to_checksum_address(contract), abi=_ERC20_BALANCE_ABI
                )
                from_amount = await loop.run_in_executor(
                    None, tc.functions.balanceOf(w3.to_checksum_address(address)).call
                )
            except Exception as e:
                logger.warning("[Retry] [%s] balanceOf failed for %s: %s", debank_key, symbol, e)
                continue

            if from_amount == 0:
                logger.info("[Retry] [%s] %s on-chain balance is 0, skipping", debank_key, symbol)
                continue

            logger.info("[Retry] [%s] Swapping %s ($%.2f) → native after refuel", debank_key, symbol, value_usd)

            try:
                connections = await loop.run_in_executor(
                    None, lifi_client.get_connections, chain_id, chain_id
                )
                if not connections:
                    continue

                quote = await loop.run_in_executor(
                    None, lifi_client.get_quote,
                    chain_id, chain_id,
                    contract, native_token_addr,
                    from_amount, address, address, settings.slippage,
                )
                tx_req = quote["transactionRequest"]

                # Простая проверка газа
                def _hi(v) -> int:
                    if not v:
                        return 0
                    if isinstance(v, str):
                        return int(v, 16) if v.startswith("0x") else int(v)
                    return int(v)

                gas_limit = _hi(tx_req.get("gasLimit") or tx_req.get("gas"))
                gas_price = _hi(tx_req.get("maxFeePerGas") or tx_req.get("gasPrice") or 0)
                tx_value = _hi(tx_req.get("value"))
                eth_needed = tx_value + gas_limit * gas_price * 2
                eth_balance = await loop.run_in_executor(None, w3.eth.get_balance, address)

                if eth_balance < eth_needed:
                    logger.warning(
                        "[Retry] [%s] Still not enough ETH for %s: have %d need %d",
                        debank_key, symbol, eth_balance, eth_needed,
                    )
                    continue

                spender = tx_req.get("to", "")
                if spender:
                    approved = await loop.run_in_executor(
                        None, ensure_erc20_approval,
                        w3, contract, address, spender, from_amount, private_key,
                    )
                    if not approved:
                        continue

                tx_hash, receipt = await loop.run_in_executor(
                    None, sign_and_send, w3, tx_req, private_key, address
                )
                if receipt is None:
                    continue

                logger.info(
                    "[Retry] [%s] Swap %s → native | tx: %s | block %s",
                    debank_key, symbol, tx_hash, receipt.blockNumber,
                )
                tokens_swapped += 1
                chain_swapped += 1
                total_usd += value_usd

                delay = random.randint(settings.delay_min, settings.delay_max)
                for _ in range(delay):
                    if stop_event.is_set():
                        break
                    await asyncio.sleep(1)

            except InsufficientFundsError:
                logger.warning("[Retry] [%s] Insufficient ETH for %s swap (node rejected)", debank_key, symbol)
            except LiFiNoRouteError as e:
                logger.info("[Retry] [%s] No route for %s: %s", debank_key, symbol, e)
            except Exception as e:
                logger.error("[Retry] [%s] Swap error for %s: %s", debank_key, symbol, e)

        if chain_swapped > 0:
            chains_processed.append(debank_key)
            logger.info("[Retry] [%s] Swapped %d tokens after refuel", debank_key, chain_swapped)

    return {
        "chains_processed": ", ".join(chains_processed),
        "tokens_swapped": tokens_swapped,
        "total_usd": round(total_usd, 2),
    }
