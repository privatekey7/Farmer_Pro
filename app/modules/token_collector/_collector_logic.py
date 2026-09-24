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
    Rabby API не возвращает поле contract_address — контракт ERC-20 хранится в поле id.
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


def _remember_gasless_chain(
    gasless_map: dict[int, dict],
    *,
    chain_id: int,
    debank_key: str,
    eth_balance: int,
    required_wei: int,
    native_token_addr: str,
    contract: str,
    symbol: str,
    value_usd: float,
) -> None:
    """Сохраняет цепь как кандидат на refuel и накапливает проблемные токены."""
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
    gc["eth_balance"] = min(gc.get("eth_balance", eth_balance), eth_balance)
    gc["max_gas_needed_wei"] = max(gc["max_gas_needed_wei"], int(required_wei))

    known_contracts = {token["contract"] for token in gc["tokens"]}
    if contract not in known_contracts:
        gc["tokens"].append({"contract": contract, "symbol": symbol, "value_usd": value_usd})


def _hi(v) -> int:
    """Парсит hex (0x-префикс) или decimal строку/int в int."""
    if not v:
        return 0
    if isinstance(v, str):
        return int(v, 16) if v.startswith("0x") else int(v)
    return int(v)


async def _swap_simulation_ok(
    loop: asyncio.AbstractEventLoop, w3: Any, tx_req: dict, address: str,
    chain_id: int, contract: str, symbol: str, log_prefix: str,
) -> bool:
    """Симуляция свопа перед отправкой. Откат → токен в чёрный список, своп не шлём.

    Котировка может выглядеть нормально (USA: $0.40 → $0.37), а своп всё равно
    откатывается — такой токен нельзя продать. Симуляция бесплатна; раньше
    ради него делался refuel ($0.26) и сжигался газ на откатившемся свопе.
    Сбой самой симуляции (не откат) своп не блокирует.
    """
    from app.modules.token_collector._bad_tokens import mark_bad
    from app.modules.token_collector._signer import simulate_tx

    ok = await loop.run_in_executor(None, simulate_tx, w3, tx_req, address)
    if ok is False:
        mark_bad(chain_id, contract, symbol, "swap simulation reverted")
        logger.warning(
            "%s Skip %s: swap simulation reverts — token can't be sold (honeypot / sell tax?), "
            "added to blocklist", log_prefix, symbol,
        )
        return False
    return True


async def _has_allowance(loop: asyncio.AbstractEventLoop, w3: Any, contract: str,
                         address: str, spender: str, amount: int) -> bool:
    """Уже выдан ли approve на amount (тогда своп можно симулировать до approve/refuel)."""
    from app.modules.token_collector._signer import get_allowance

    if not spender:
        return True
    try:
        return await loop.run_in_executor(None, get_allowance, w3, contract, address, spender) >= amount
    except Exception:
        return False


async def _estimate_swap_tx_cost(
    loop: asyncio.AbstractEventLoop,
    w3: Any,
    tx_req: dict,
) -> tuple[int, int, int, int]:
    """
    Возвращает (gas_limit, effective_gas_price, tx_value, l1_data_fee) для swap tx.
    Логика зеркалирует sign_and_send и добавляет L1 data fee для OP-stack цепей.
    effective_gas_price — МИНИМАЛЬНЫЙ рабочий потолок (baseFee × 1.1 + priority):
    по нему решается, хватает ли газа. Потолок ×2 sign_and_send ставит только
    если баланс позволяет (_fit_max_fee). Раньше проверка шла по ×2, и кошельки,
    которым газа хватало, уходили в refuel (на arb — лишний перевод $0.73).
    """
    from app.modules.token_collector._bridge_logic import _get_l1_fee_safe
    from app.modules.token_collector._signer import MIN_MAX_FEE_PCT, effective_priority

    gas_limit = _hi(tx_req.get("gasLimit") or tx_req.get("gas"))
    tx_value = _hi(tx_req.get("value"))

    if "maxFeePerGas" in tx_req:
        priority = await loop.run_in_executor(
            None, effective_priority, w3, _hi(tx_req.get("maxPriorityFeePerGas"))
        )
        try:
            base_fee = await loop.run_in_executor(
                None, lambda: w3.eth.get_block("latest")["baseFeePerGas"]
            )
            effective_gas_price = base_fee * MIN_MAX_FEE_PCT // 100 + priority
        except Exception:
            quote_max_fee = _hi(tx_req.get("maxFeePerGas"))
            effective_gas_price = max(quote_max_fee * 2, priority)
    else:
        quote_gas_price = _hi(tx_req.get("gasPrice"))
        try:
            base_fee = await loop.run_in_executor(
                None, lambda: w3.eth.get_block("latest").get("baseFeePerGas") or 0
            )
            effective_gas_price = max(quote_gas_price, base_fee * 2) if base_fee else quote_gas_price * 2
        except Exception:
            effective_gas_price = quote_gas_price * 2 if quote_gas_price else 0

    l1_data_fee = await loop.run_in_executor(None, _get_l1_fee_safe, w3, tx_req)
    return gas_limit, effective_gas_price, tx_value, l1_data_fee


async def fetch_and_swap(
    wallet: dict,                               # {"raw": "0x...", "type": "private_key"}
    lifi_client: Any,                           # LiFiClient
    proxy_rotator: Any,                         # ProxyRotator
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
    ШАГ 1-2: получить балансы (Rabby API), отфильтровать,
    своп не-нативных токенов в нативный через LI.FI.
    Возвращает статистику: chains_processed, chains_skipped, tokens_swapped, total_usd.
    """
    from app.integrations.balance_verifier import check_wallet
    from app.integrations.lifi_client import DEBANK_TO_CHAIN_ID, LiFiNoRouteError
    from app.modules.token_collector._signer import (
        derive_address, sign_and_send, ensure_erc20_approval, InsufficientFundsError, TransactionReverted,
    )
    from app.modules.token_collector._bad_tokens import is_bad
    from app.core.logger import SUCCESS

    address, private_key = derive_address(wallet["raw"], wallet["type"])
    loop = asyncio.get_running_loop()

    # ШАГ 1: Проверенные балансы (защита от фантомов — balance_verifier):
    # токены из cache_token_list, каждый ≥ $0.5 подтверждён on-chain; список
    # принимается, когда сошлись 2 независимые выборки через разные прокси.
    verified = await loop.run_in_executor(
        None,
        lambda: check_wallet(address, proxy_rotator, stop_event, with_positions=False),
    )
    if stop_event.is_set():
        return {}
    if verified["status"] == "ERROR":
        logger.error("[%s] Rabby balances failed: %s", address[:10], verified["error"])
        return {}
    tokens: list[dict] = verified["tokens"]
    for note in verified["notes"]:
        logger.info("[Wallet %s] %s", address[:10], note)
    if verified["status"] == "UNVERIFIED":
        # Все токены в списке подтверждены on-chain (фантомов нет), но выборки
        # не сошлись — возможно, часть токенов кошелька не попала в список.
        logger.warning(
            "[Wallet %s] Rabby balances NOT corroborated (%s) — берём консервативный "
            "список ($%.2f), своп идёт только по on-chain balanceOf",
            address[:10], verified["error"], verified["tokens_usd"],
        )

    # Группируем по chain
    chains: dict[str, list[dict]] = {}
    for t in tokens:
        chains.setdefault(t.get("chain", ""), []).append(t)

    chains_processed: list[str] = []
    chains_skipped: list[str] = []
    tokens_swapped = 0
    swaps_attempted = 0          # свопы, для которых реально ушла транзакция
    ops: list[dict] = []         # все отправленные транзакции (approve/swap) — для экспорта
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
            log_prefix = f"[Wallet {address[:10]}] [{debank_key}]"

            if is_bad(chain_id, contract):
                logger.info("%s Skip %s: in blocklist — swap reverted before (can't be sold)", log_prefix, symbol)
                continue

            # Получаем w3 и реальный on-chain баланс — кэш Rabby может быть устаревшим
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
                    logger.warning("[Wallet %s] [%s] balanceOf failed for %s, using API amount: %s",
                                   address[:10], debank_key, symbol, e2)
                    amount = token.get("amount", 0)
                    from_amount = int(Decimal(str(amount)) * Decimal(10 ** decimals))

            if from_amount == 0:
                logger.info("[Wallet %s] [%s] Skip %s: on-chain balance is 0", address[:10], debank_key, symbol)
                continue

            logger.info("[Wallet %s] [%s] Swapping %s ($%.2f) → native", address[:10], debank_key, symbol, value_usd)

            gas_limit = 0
            effective_gas_price = 0
            tx_value = 0
            eth_needed = 0
            eth_balance = 0
            l1_data_fee = 0
            spender = ""
            approval_done = False
            tx_req: dict[str, Any] = {}
            APPROVE_GAS_BUFFER = 100_000

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

                # Approve уже выдан → симулируем своп сразу, ДО проверки газа:
                # непродаваемый токен не должен вызывать refuel.
                allowance_ok = await _has_allowance(loop, w3, contract, address, tx_req.get("to", ""), from_amount)
                if allowance_ok and not await _swap_simulation_ok(
                    loop, w3, tx_req, address, chain_id, contract, symbol, log_prefix,
                ):
                    continue

                gas_limit, effective_gas_price, tx_value, l1_data_fee = await _estimate_swap_tx_cost(
                    loop, w3, tx_req
                )
                # Резерв на отдельный approve tx. На OP-stack учитываем и его L1 data fee.
                approve_reserve = APPROVE_GAS_BUFFER * effective_gas_price
                if tx_req.get("to"):
                    approve_reserve += l1_data_fee
                eth_needed = tx_value + gas_limit * effective_gas_price + l1_data_fee + approve_reserve
                eth_balance = await loop.run_in_executor(None, w3.eth.get_balance, address)

                # +10% запас на флуктуацию baseFee между предпроверкой и отправкой
                if eth_balance < int(eth_needed * 1.1):
                    logger.warning(
                        "[Wallet %s] [%s] Skip %s swap: ETH balance %d < needed %d "
                        "(gas_limit=%d gas_price=%d l1_fee=%d approve_buf=%d value=%d)",
                        address[:10], debank_key, symbol,
                        eth_balance, eth_needed,
                        gas_limit, effective_gas_price,
                        l1_data_fee,
                        APPROVE_GAS_BUFFER * effective_gas_price,
                        tx_value,
                    )
                    _remember_gasless_chain(
                        gasless_map,
                        chain_id=chain_id,
                        debank_key=debank_key,
                        eth_balance=eth_balance,
                        required_wei=int(eth_needed * 1.1),
                        native_token_addr=native_token_addr,
                        contract=contract,
                        symbol=symbol,
                        value_usd=value_usd,
                    )
                    continue

                # ERC-20 approve: даём разрешение LI.FI diamond тратить токен
                spender = tx_req.get("to", "")
                approval_done = not bool(spender)
                if spender:
                    approved = await loop.run_in_executor(
                        None, lambda: ensure_erc20_approval(
                            w3, contract, address, spender, from_amount, private_key,
                            ops=ops, chain=debank_key, label=symbol,
                        ),
                    )
                    if not approved:
                        logger.error(
                            "[Wallet %s] [%s] Approve failed for %s, skipping swap",
                            address[:10], debank_key, symbol,
                        )
                        continue
                    approval_done = True

                # Симуляция после свежего approve (до него своп откатился бы в любом случае)
                if not allowance_ok and not await _swap_simulation_ok(
                    loop, w3, tx_req, address, chain_id, contract, symbol, log_prefix,
                ):
                    continue

                swaps_attempted += 1
                swap_op = {"type": "swap", "chain": debank_key, "detail": f"{symbol} → native",
                           "usd": round(value_usd, 4), "tx": "", "status": "PENDING"}
                ops.append(swap_op)
                try:
                    tx_hash, receipt = await loop.run_in_executor(
                        None, sign_and_send, w3, tx_req, private_key, address
                    )
                except TransactionReverted as e:
                    swap_op.update(tx=e.tx_hash, status="REVERTED")
                    logger.error("[Wallet %s] [%s] Swap %s reverted: %s", address[:10], debank_key, symbol, e.tx_hash)
                    continue
                except Exception:
                    ops.remove(swap_op)  # tx не ушла (нода отклонила) — не попытка
                    swaps_attempted -= 1
                    raise
                swap_op["tx"] = tx_hash

                if receipt is None:
                    # Не попала в блок за время ожидания — исход неизвестен
                    logger.warning(
                        "[Wallet %s] [%s] Swap tx not confirmed: %s",
                        address[:10], debank_key, tx_hash,
                    )
                    continue

                swap_op["status"] = "CONFIRMED"
                logger.log(
                    SUCCESS,
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
                current_balance = eth_balance
                try:
                    current_balance = await loop.run_in_executor(None, w3.eth.get_balance, address)
                except Exception as balance_error:
                    logger.debug(
                        "[Wallet %s] [%s] Could not refresh ETH balance after insufficient funds: %s",
                        address[:10], debank_key, balance_error,
                    )

                if tx_req:
                    gas_limit, effective_gas_price, tx_value, l1_data_fee = await _estimate_swap_tx_cost(
                        loop, w3, tx_req
                    )

                remaining_needed = tx_value + gas_limit * effective_gas_price + l1_data_fee
                if approval_done:
                    required_wei = remaining_needed
                else:
                    approve_reserve = APPROVE_GAS_BUFFER * effective_gas_price
                    if spender:
                        approve_reserve += l1_data_fee
                    required_wei = remaining_needed + approve_reserve

                # Нода уже отвергла tx, значит наша локальная оценка всё ещё занижена
                # (например из-за L1 fee или роста baseFee). Делаем гарантированно
                # положительный дефицит, чтобы refuel не ушёл в отрицательное значение.
                if required_wei <= current_balance:
                    shortfall_floor = max(
                        l1_data_fee,
                        gas_limit * max(effective_gas_price, 1) // 10,
                        1,
                    )
                    required_wei = current_balance + shortfall_floor

                if required_wei > 0:
                    _remember_gasless_chain(
                        gasless_map,
                        chain_id=chain_id,
                        debank_key=debank_key,
                        eth_balance=current_balance,
                        required_wei=int(required_wei * 1.1),
                        native_token_addr=native_token_addr,
                        contract=contract,
                        symbol=symbol,
                        value_usd=value_usd,
                    )
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
        "swaps_attempted": swaps_attempted,
        "total_collected_usd": round(total_usd, 2),
        "gasless_chains": list(gasless_map.values()),
        "ops": ops,
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
    Не вызывает Rabby — использует список токенов из gasless_chains.
    Возвращает {chains_processed, tokens_swapped, swaps_attempted, total_usd, ops}.
    """
    from app.integrations.lifi_client import DEBANK_TO_CHAIN_ID, LiFiNoRouteError
    from app.modules.token_collector._signer import (
        sign_and_send, ensure_erc20_approval, InsufficientFundsError, TransactionReverted,
    )
    from app.modules.token_collector._bad_tokens import is_bad
    from app.core.logger import SUCCESS

    loop = asyncio.get_running_loop()
    chains_processed: list[str] = []
    tokens_swapped = 0
    swaps_attempted = 0
    ops: list[dict] = []
    total_usd = 0.0
    APPROVE_GAS_BUFFER = 100_000

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
            if is_bad(chain_id, contract):
                logger.info("[Retry] [%s] Skip %s: in blocklist", debank_key, symbol)
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
                log_prefix = f"[Retry] [{debank_key}]"
                allowance_ok = await _has_allowance(loop, w3, contract, address, tx_req.get("to", ""), from_amount)
                if allowance_ok and not await _swap_simulation_ok(
                    loop, w3, tx_req, address, chain_id, contract, symbol, log_prefix,
                ):
                    continue

                # Та же оценка, что и в основном проходе: свежий baseFee + L1 fee
                # (maxFeePerGas из котировки бывает заглушкой) + резерв на approve.
                gas_limit, effective_gas_price, tx_value, l1_data_fee = await _estimate_swap_tx_cost(
                    loop, w3, tx_req
                )
                approve_reserve = APPROVE_GAS_BUFFER * effective_gas_price
                if tx_req.get("to"):
                    approve_reserve += l1_data_fee
                eth_needed = int(
                    (tx_value + gas_limit * effective_gas_price + l1_data_fee + approve_reserve) * 1.1
                )
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
                        None, lambda: ensure_erc20_approval(
                            w3, contract, address, spender, from_amount, private_key,
                            ops=ops, chain=debank_key, label=symbol,
                        ),
                    )
                    if not approved:
                        logger.error("[Retry] [%s] Approve failed for %s, skipping swap", debank_key, symbol)
                        continue

                if not allowance_ok and not await _swap_simulation_ok(
                    loop, w3, tx_req, address, chain_id, contract, symbol, log_prefix,
                ):
                    continue

                swaps_attempted += 1
                swap_op = {"type": "swap", "chain": debank_key, "detail": f"{symbol} → native",
                           "usd": round(value_usd, 4), "tx": "", "status": "PENDING"}
                ops.append(swap_op)
                try:
                    tx_hash, receipt = await loop.run_in_executor(
                        None, sign_and_send, w3, tx_req, private_key, address
                    )
                except TransactionReverted as e:
                    swap_op.update(tx=e.tx_hash, status="REVERTED")
                    logger.error("[Retry] [%s] Swap %s reverted: %s", debank_key, symbol, e.tx_hash)
                    continue
                except Exception:
                    ops.remove(swap_op)
                    swaps_attempted -= 1
                    raise
                swap_op["tx"] = tx_hash
                if receipt is None:
                    logger.warning("[Retry] [%s] Swap tx not confirmed: %s", debank_key, tx_hash)
                    continue

                swap_op["status"] = "CONFIRMED"
                logger.log(
                    SUCCESS,
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
        "swaps_attempted": swaps_attempted,
        "total_usd": round(total_usd, 2),
        "ops": ops,
    }
