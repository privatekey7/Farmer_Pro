# app/modules/token_collector/_bridge_logic.py
from __future__ import annotations
import asyncio
import logging
import random
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# Таймаут ожидания бриджа — 30 минут
BRIDGE_TIMEOUT_SEC = 30 * 60

# OP-stack GasPriceOracle — одинаковый адрес на всех OP-stack цепях
# (OP, Base, INK, Unichain, Mode, Lisk, Soneium, World, Zora...)
_L1_GAS_ORACLE = "0x420000000000000000000000000000000000000F"
_L1_FEE_ABI = [
    {
        "inputs": [{"internalType": "bytes", "name": "_data", "type": "bytes"}],
        "name": "getL1Fee",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]


def _hi(v) -> int:
    """Парсит hex (0x-префикс) или decimal строку/int в int."""
    if not v:
        return 0
    if isinstance(v, str):
        return int(v, 16) if v.startswith("0x") else int(v)
    return int(v)


def _tx_gas_cost(tx_req: dict) -> int:
    """Реальная стоимость газа из transactionRequest: gasLimit × maxFeePerGas (или gasPrice)."""

    gas_limit = _hi(tx_req.get("gasLimit") or tx_req.get("gas"))
    gas_price = _hi(tx_req.get("maxFeePerGas") or tx_req.get("gasPrice"))
    return gas_limit * gas_price


def _get_l1_fee_safe(w3: Any, tx_req: dict) -> int:
    """
    Оценивает L1 data fee для OP-stack цепей через GasPriceOracle.getL1Fee().
    На ETH mainnet, ARB и других non-OP-stack цепях оракул отсутствует → возвращает 0.

    Передаём ~300 байт RLP-заголовка (нули) + calldata — это приближение реального
    размера подписанной транзакции. Нули стоят 4 gas/byte (дешевле реального заголовка),
    поэтому оценка чуть занижена — умножаем на 1.1 для запаса.
    """
    from web3 import Web3
    try:
        oracle = w3.eth.contract(
            address=Web3.to_checksum_address(_L1_GAS_ORACLE),
            abi=_L1_FEE_ABI,
        )
        data_hex = tx_req.get("data") or "0x"
        calldata = bytes.fromhex(data_hex[2:] if data_hex.startswith("0x") else data_hex)
        # ~300 байт RLP-заголовка (нули) + calldata ≈ реальный размер tx
        tx_bytes = bytes(300) + calldata
        fee = int(oracle.functions.getL1Fee(tx_bytes).call())
        # +10% запас: заголовок содержит ненулевые байты (дороже чем нули в нашей аппроксимации)
        return fee * 11 // 10
    except Exception:
        return 0  # Non-OP-stack цепь или оракул недоступен


async def bridge_native(
    address: str,
    private_key: str,
    lifi_client: Any,                   # LiFiClient
    relay_client: Any,                  # RelayClient
    rpc_resolver: Any,                  # RpcResolver
    settings: Any,                      # CollectorSettings
    lifi_chain_ids: set[int],
    relay_chain_ids: set[int],
    native_token_by_id: dict[int, dict],
    relay_native_by_id: dict[int, dict],
    target_chain_ids: list[int],
    src_chain_id: int,
    gas_prices: dict,
    stop_event: threading.Event,
) -> tuple[str | None, str, int | None, float]:
    """
    ШАГ 3: бридж нативного токена из src_chain_id в target_chain.
    Возвращает (bridge_tx, bridge_status, tgt_chain_id, sent_usd).
    sent_usd — USD-стоимость отправленной суммы при статусе COMPLETED, иначе 0.0.
    """
    from app.integrations.lifi_client import LiFiNoRouteError
    from app.integrations.relay_client import RelayNoRouteError
    from app.modules.token_collector._signer import sign_and_send, TransactionReverted

    loop = asyncio.get_running_loop()

    tgt_chain_id = random.choice(target_chain_ids)
    logger.info("[Wallet %s] Bridge: chain %s → %s", address[:10], src_chain_id, tgt_chain_id)

    # Определяем доступных провайдеров
    lifi_ok = src_chain_id in lifi_chain_ids and tgt_chain_id in lifi_chain_ids
    relay_ok = src_chain_id in relay_chain_ids and tgt_chain_id in relay_chain_ids

    if not lifi_ok and not relay_ok:
        logger.info(
            "[Wallet %s] No bridge route %s → %s on any provider",
            address[:10], src_chain_id, tgt_chain_id
        )
        return None, "NO_ROUTE", None, 0.0

    # Проверяем LI.FI connections
    if lifi_ok:
        try:
            connections = await loop.run_in_executor(
                None, lifi_client.get_connections, src_chain_id, tgt_chain_id
            )
            if not connections:
                lifi_ok = False
        except Exception as e:
            logger.warning(
                "[Wallet %s] LI.FI connections check failed (%s → %s): %s",
                address[:10], src_chain_id, tgt_chain_id, e,
            )
            lifi_ok = False

    # Адреса нативных токенов
    src_native = (
        native_token_by_id.get(src_chain_id, {}).get("address")
        or relay_native_by_id.get(src_chain_id, {}).get("address")
        or "0x0000000000000000000000000000000000000000"
    )
    tgt_native = (
        native_token_by_id.get(tgt_chain_id, {}).get("address")
        or relay_native_by_id.get(tgt_chain_id, {}).get("address")
        or "0x0000000000000000000000000000000000000000"
    )

    try:
        w3 = rpc_resolver.get_web3(src_chain_id)
    except RuntimeError as e:
        logger.warning("[Wallet %s] No RPC available for chain %s: %s", address[:10], src_chain_id, e)
        return None, "NO_RPC", None, 0.0

    # Получаем баланс с ротацией при сбое RPC
    try:
        balance_wei = await loop.run_in_executor(None, w3.eth.get_balance, address)
    except Exception as e:
        logger.warning("[Wallet %s] RPC error on get_balance chain %s (%s), rotating...", address[:10], src_chain_id, e)
        try:
            w3 = rpc_resolver.rotate(src_chain_id)
            balance_wei = await loop.run_in_executor(None, w3.eth.get_balance, address)
        except Exception as e2:
            logger.error("[Wallet %s] All RPCs failed for chain %s: %s", address[:10], src_chain_id, e2)
            return None, "NO_RPC", None, 0.0

    # Цена нативного токена (нужна и для min_bridge_usd и для расчёта sent_usd)
    price_usd = float(
        native_token_by_id.get(src_chain_id, {}).get("priceUSD", 0)
        or relay_native_by_id.get(src_chain_id, {}).get("priceUSD", 0)
        or 0
    )

    # Проверка минимальной суммы для бриджа
    if settings.min_bridge_usd > 0:
        balance_usd = (balance_wei / 1e18) * price_usd
        # Допуск 0.001$ чтобы float precision не отсекал пограничные значения ($0.10 vs $0.09999...)
        if price_usd > 0 and balance_usd < settings.min_bridge_usd - 0.001:
            logger.info(
                "[Wallet %s] Bridge skipped: balance $%.4f < min_bridge_usd $%.2f",
                address[:10], balance_usd, settings.min_bridge_usd,
            )
            return None, "BELOW_MIN", None, 0.0

    # Пробные котировки для сравнения
    lifi_out = 0
    lifi_gas = 0
    lifi_l1_fee = 0
    relay_out = 0
    relay_gas = 0
    relay_l1_fee = 0

    if lifi_ok:
        try:
            lifi_q = await loop.run_in_executor(
                None,
                lifi_client.get_quote,
                src_chain_id, tgt_chain_id,
                src_native, tgt_native,
                balance_wei, address, address, settings.slippage,
            )
            # Берём газ из transactionRequest — реальная стоимость source tx (gasLimit × gasPrice).
            # gas_suggestion от LI.FI НЕ используем: это "рекомендованный резерв" для многих
            # будущих транзакций, он в 5-50x превышает стоимость одного бриджа и блокирует мелкие кошельки.
            lifi_tx_req_probe = lifi_q.get("transactionRequest", {})
            lifi_gas = _tx_gas_cost(lifi_tx_req_probe)
            if lifi_gas == 0:
                lifi_gas = sum(int(g["amount"]) for g in lifi_q.get("estimate", {}).get("gasCosts", []))
            lifi_out = int(lifi_q.get("estimate", {}).get("toAmountMin", 0))
            # L1 data fee для OP-stack (на ETH/ARB/etc. возвращает 0)
            lifi_l1_fee = await loop.run_in_executor(None, _get_l1_fee_safe, w3, lifi_tx_req_probe)
        except (LiFiNoRouteError, Exception) as e:
            logger.warning("[Wallet %s] LI.FI quote failed: %s", address[:10], e)
            lifi_ok = False

    if relay_ok:
        try:
            relay_src_native = (
                relay_native_by_id.get(src_chain_id, {}).get("address")
                or "0x0000000000000000000000000000000000000000"
            )
            relay_tgt_native = (
                relay_native_by_id.get(tgt_chain_id, {}).get("address")
                or "0x0000000000000000000000000000000000000000"
            )
            relay_q = await loop.run_in_executor(
                None,
                relay_client.get_quote,
                src_chain_id, tgt_chain_id,
                relay_src_native, relay_tgt_native,
                str(balance_wei), address,
            )
            # Берём газ из tx_req source-транзакции (gasLimit × gasPrice).
            # fees["relayer"] — протокольная комиссия, вычитается из VALUE (output),
            # не требует дополнительного ETH в кошельке — не включаем в gas_reserve.
            relay_tx_req = relay_q["steps"][0]["items"][0]["data"]
            relay_gas = _tx_gas_cost(relay_tx_req)
            if relay_gas == 0:
                relay_gas = int(relay_q["fees"]["gas"]["amount"])
            relay_out = int(relay_q["details"]["currencyOut"]["amount"])
            # L1 data fee для OP-stack (на ETH/ARB/etc. возвращает 0)
            relay_l1_fee = await loop.run_in_executor(None, _get_l1_fee_safe, w3, relay_tx_req)
        except Exception as e:
            logger.warning("[Wallet %s] Relay quote failed: %s", address[:10], e)
            relay_ok = False

    if not lifi_ok and not relay_ok:
        return None, "NO_QUOTE", None, 0.0

    # Выбор провайдера по максимальному out
    if lifi_ok and relay_ok:
        provider = "lifi" if lifi_out >= relay_out else "relay"
    elif lifi_ok:
        provider = "lifi"
    else:
        provider = "relay"

    chosen_gas = lifi_gas if provider == "lifi" else relay_gas
    chosen_l1_fee = lifi_l1_fee if provider == "lifi" else relay_l1_fee
    chosen_out = lifi_out if provider == "lifi" else relay_out
    if chosen_l1_fee:
        logger.info(
            "[Bridge] provider=%s out=%.6f (lifi=%s relay=%s) l1_fee=%d",
            provider, chosen_out / 1e18, lifi_out, relay_out, chosen_l1_fee,
        )
    else:
        logger.info(
            "[Bridge] provider=%s out=%.6f (lifi=%s relay=%s)",
            provider, chosen_out / 1e18, lifi_out, relay_out,
        )

    # gas_reserve = source tx gas × 2 (запас на случай спайка baseFee)
    # + L1 data fee для OP-stack цепей (на ETH/ARB/etc. = 0).
    gas_reserve = chosen_gas * 2 + chosen_l1_fee

    if gas_reserve == 0:
        # Последний fallback: web3 gas_price × лимит для bridge tx
        gas_price_wei = await loop.run_in_executor(None, lambda: w3.eth.gas_price)
        gas_reserve = gas_price_wei * 500_000
        logger.info("[Wallet %s] Gas reserve was 0, fallback: %d wei", address[:10], gas_reserve)

    send_amount = balance_wei - gas_reserve

    logger.info(
        "[Wallet %s] Gas calc: source_gas=%d reserve=%d balance=%d send=%d ($%.4f)",
        address[:10], chosen_gas, gas_reserve, balance_wei, send_amount,
        send_amount / 1e18 * float(
            native_token_by_id.get(src_chain_id, {}).get("priceUSD", 0) or 0
        ),
    )

    if send_amount <= 0:
        logger.info("[Wallet %s] Insufficient balance for bridge after gas reserve", address[:10])
        return None, "INSUFFICIENT", tgt_chain_id, 0.0

    # Финальная котировка
    try:
        if provider == "lifi":
            final_quote = await loop.run_in_executor(
                None,
                lifi_client.get_quote,
                src_chain_id, tgt_chain_id,
                src_native, tgt_native,
                send_amount, address, address, settings.slippage,
            )
            tx_req = final_quote["transactionRequest"]
        else:
            relay_src_native = (
                relay_native_by_id.get(src_chain_id, {}).get("address")
                or "0x0000000000000000000000000000000000000000"
            )
            relay_tgt_native = (
                relay_native_by_id.get(tgt_chain_id, {}).get("address")
                or "0x0000000000000000000000000000000000000000"
            )
            final_quote = await loop.run_in_executor(
                None,
                relay_client.get_quote,
                src_chain_id, tgt_chain_id,
                relay_src_native, relay_tgt_native,
                str(send_amount), address,
            )
            tx_req = final_quote["steps"][0]["items"][0]["data"]
    except Exception as e:
        logger.warning(
            "[Wallet %s] Final quote failed (provider=%s, send_amount=%d): %s",
            address[:10], provider, send_amount, e,
        )
        return None, "NO_QUOTE", tgt_chain_id, 0.0

    # Safety-check: вычисляем gas cost так же как sign_and_send —
    # читаем свежий baseFee и применяем priority из котировки.
    # ВАЖНО: tx_req.maxFeePerGas у Relay может быть placeholder (0 или 1 wei) —
    # нельзя использовать как fallback; нужен реальный baseFee из сети.
    gas_limit = _hi(tx_req.get("gasLimit") or tx_req.get("gas"))
    base_fee: int | None = None
    try:
        base_fee = await loop.run_in_executor(None, lambda: w3.eth.get_block("latest")["baseFeePerGas"])
    except Exception as _e1:
        logger.debug("[Wallet %s] get_block failed in safety check (%s), trying gas_price...", address[:10], _e1)
        try:
            base_fee = await loop.run_in_executor(None, lambda: w3.eth.gas_price)
        except Exception as _e2:
            logger.warning("[Wallet %s] Cannot determine gas price for safety check (%s) — skipping bridge", address[:10], _e2)
            return None, "INSUFFICIENT", tgt_chain_id, 0.0

    if "maxFeePerGas" in tx_req:
        priority = max(1, _hi(tx_req.get("maxPriorityFeePerGas")))
        effective_gas_price = base_fee * 11 // 10 + priority
    else:
        quote_gp = _hi(tx_req.get("gasPrice"))
        effective_gas_price = max(quote_gp, base_fee * 2) if base_fee else quote_gp * 2

    final_gas_cost = gas_limit * effective_gas_price
    # L1 data fee для финальной котировки (OP-stack цепи)
    l1_data_fee = await loop.run_in_executor(None, _get_l1_fee_safe, w3, tx_req)
    tx_value = _hi(tx_req.get("value"))
    logger.info(
        "[Wallet %s] Safety check: value=%d gas_limit=%d gas_price=%d gas_cost=%d l1_fee=%d balance=%d",
        address[:10], tx_value, gas_limit, effective_gas_price, final_gas_cost, l1_data_fee, balance_wei,
    )
    if tx_value + final_gas_cost + l1_data_fee > balance_wei:
        logger.warning(
            "[Wallet %s] Final tx exceeds balance (value=%d gas=%d l1_fee=%d price=%d balance=%d), skipping",
            address[:10], tx_value, gas_limit, l1_data_fee, effective_gas_price, balance_wei,
        )
        return None, "INSUFFICIENT", tgt_chain_id, 0.0

    # Снимаем баланс на destination chain ДО отправки tx (Relay доставляет за секунды!)
    pre_balance = 0
    try:
        w3_tgt = rpc_resolver.get_web3(tgt_chain_id)
        pre_balance = await loop.run_in_executor(None, w3_tgt.eth.get_balance, address)
    except Exception as e:
        logger.warning("[Wallet %s] Could not get pre-bridge balance on chain %s: %s", address[:10], tgt_chain_id, e)

    try:
        tx_hash, receipt = await loop.run_in_executor(
            None, sign_and_send, w3, tx_req, private_key, address
        )
    except TransactionReverted as e:
        logger.warning("[Wallet %s] Bridge tx reverted: %s", address[:10], e.tx_hash)
        return e.tx_hash, "TX_REVERTED", tgt_chain_id, 0.0
    except Exception as e:
        e_str = str(e)
        if "-32000" in e_str:
            logger.warning("[Wallet %s] Bridge tx rejected by node: %s", address[:10], e_str[:160])
            return None, "NODE_REJECTED", tgt_chain_id, 0.0
        raise

    # Ожидаемый минимум поступления (50% от quoted toAmountMin — консервативный порог)
    if provider == "lifi":
        to_amount_min = int(final_quote.get("estimate", {}).get("toAmountMin", 0))
    else:
        to_amount_min = int(relay_q.get("details", {}).get("currencyOut", {}).get("minimumAmount", 0) or 0)
    expected_min = int(to_amount_min * 0.5) if to_amount_min > 0 else 1

    logger.info(
        "[Wallet %s] Bridge tx: %s | pre_balance=%d expected_min=%d wei",
        address[:10], tx_hash, pre_balance, expected_min,
    )

    status = await _poll_balance_increase(
        rpc_resolver=rpc_resolver,
        address=address,
        tgt_chain_id=tgt_chain_id,
        pre_balance=pre_balance,
        expected_min=expected_min,
        loop=loop,
        stop_event=stop_event,
    )

    # Если tx отправлена, но RPC destination упал — не помечаем как ошибку.
    # "SENT" = транзакция в сети, мониторинг недоступен.
    if status == "NO_RPC":
        logger.warning(
            "[Wallet %s] Bridge tx %s sent but destination RPC unavailable — marking SENT",
            address[:10], tx_hash,
        )
        status = "SENT"

    sent_usd = round(send_amount / 1e18 * price_usd, 4) if status in ("COMPLETED", "SENT") else 0.0
    return tx_hash, status, tgt_chain_id, sent_usd


async def _poll_balance_increase(
    rpc_resolver: Any,
    address: str,
    tgt_chain_id: int,
    pre_balance: int,
    expected_min: int,
    loop: asyncio.AbstractEventLoop,
    stop_event: threading.Event,
) -> str:
    """
    Опрашивает баланс на destination chain каждые 10 сек.
    Возвращает COMPLETED когда баланс вырос на expected_min, TIMEOUT через 30 мин.
    Надёжнее API-поллинга: не зависит от скорости индексации LI.FI / Relay.
    """
    POLL_INTERVAL = 10
    elapsed = 0

    try:
        w3 = rpc_resolver.get_web3(tgt_chain_id)
    except Exception as e:
        logger.warning("[Bridge] No RPC for destination chain %s: %s", tgt_chain_id, e)
        return "NO_RPC"

    while elapsed < BRIDGE_TIMEOUT_SEC:
        if stop_event.is_set():
            return "STOPPED"
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

        try:
            balance = await loop.run_in_executor(None, w3.eth.get_balance, address)
            increase = balance - pre_balance
            if increase >= expected_min:
                logger.info(
                    "[Wallet %s] Bridge COMPLETED: balance on chain %s +%.6f ETH (after %ds)",
                    address[:10], tgt_chain_id, increase / 1e18, elapsed,
                )
                return "COMPLETED"
            if elapsed % 60 == 0:  # лог раз в минуту
                logger.info(
                    "[Wallet %s] Waiting bridge on chain %s... +%.8f ETH / need %.8f ETH (%ds)",
                    address[:10], tgt_chain_id,
                    max(increase, 0) / 1e18, expected_min / 1e18, elapsed,
                )
        except Exception as e:
            logger.warning("[Wallet %s] Balance check error on chain %s: %s — rotating RPC", address[:10], tgt_chain_id, e)
            try:
                w3 = rpc_resolver.rotate(tgt_chain_id)
            except RuntimeError:
                logger.error("[Wallet %s] All RPCs exhausted for chain %s", address[:10], tgt_chain_id)
                return "NO_RPC"

    logger.error("[Wallet %s] Bridge timeout after %ds", address[:10], BRIDGE_TIMEOUT_SEC)
    return "TIMEOUT"


async def refuel_chain(
    address: str,
    private_key: str,
    donor_chain_id: int,
    tgt_chain_id: int,
    refuel_amount_wei: int,
    relay_client: Any,
    relay_native_by_id: dict[int, dict],
    rpc_resolver: Any,
    stop_event: threading.Event,
) -> bool:
    """
    ШАГ 2.5: отправляет небольшое количество нативного токена из donor_chain_id
    в tgt_chain_id через Relay, чтобы покрыть газ для последующих свапов.
    Возвращает True если средства успешно доставлены.
    """
    from app.modules.token_collector._signer import sign_and_send

    REFUEL_TIMEOUT_SEC = 3 * 60  # 3 минуты
    POLL_INTERVAL = 10

    loop = asyncio.get_running_loop()

    donor_native = (
        relay_native_by_id.get(donor_chain_id, {}).get("address")
        or "0x0000000000000000000000000000000000000000"
    )
    tgt_native = (
        relay_native_by_id.get(tgt_chain_id, {}).get("address")
        or "0x0000000000000000000000000000000000000000"
    )

    logger.info(
        "[Refuel] %s → chain %s: sending %d wei (%s ETH) for gas",
        donor_chain_id, tgt_chain_id, refuel_amount_wei, refuel_amount_wei / 1e18,
    )

    try:
        relay_q = await loop.run_in_executor(
            None,
            relay_client.get_quote,
            donor_chain_id, tgt_chain_id,
            donor_native, tgt_native,
            str(refuel_amount_wei), address,
        )
        tx_req = relay_q["steps"][0]["items"][0]["data"]
    except Exception as e:
        logger.warning("[Refuel] Relay quote failed (%s → %s): %s", donor_chain_id, tgt_chain_id, e)
        return False

    try:
        w3_donor = rpc_resolver.get_web3(donor_chain_id)
    except Exception as e:
        logger.warning("[Refuel] No RPC for donor chain %s: %s", donor_chain_id, e)
        return False

    # Снимаем pre_balance на tgt_chain ДО отправки
    pre_balance = 0
    try:
        w3_tgt = rpc_resolver.get_web3(tgt_chain_id)
        pre_balance = await loop.run_in_executor(None, w3_tgt.eth.get_balance, address)
    except Exception as e:
        logger.warning("[Refuel] Could not get pre-balance on chain %s: %s", tgt_chain_id, e)

    try:
        tx_hash, _ = await loop.run_in_executor(
            None, sign_and_send, w3_donor, tx_req, private_key, address
        )
    except Exception as e:
        logger.warning("[Refuel] Failed to send refuel tx: %s", e)
        return False

    logger.info("[Refuel] Tx sent: %s — waiting for arrival on chain %s...", tx_hash, tgt_chain_id)

    # Ожидаем поступления
    expected_min = max(1, int(refuel_amount_wei * 0.3))  # 30% от отправленного
    elapsed = 0
    while elapsed < REFUEL_TIMEOUT_SEC:
        if stop_event.is_set():
            return False
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        try:
            balance = await loop.run_in_executor(None, w3_tgt.eth.get_balance, address)
            if balance - pre_balance >= expected_min:
                logger.info(
                    "[Refuel] Arrived on chain %s: +%.8f ETH (after %ds)",
                    tgt_chain_id, (balance - pre_balance) / 1e18, elapsed,
                )
                return True
        except Exception as e:
            logger.warning("[Refuel] Balance check error on chain %s: %s", tgt_chain_id, e)
            try:
                w3_tgt = rpc_resolver.rotate(tgt_chain_id)
            except RuntimeError:
                return False

    logger.warning("[Refuel] Timeout waiting for arrival on chain %s", tgt_chain_id)
    return False


async def send_to_exchange(
    address: str,
    private_key: str,
    exchange_address: str,
    tgt_chain_id: int,
    rpc_resolver: Any,
    gas_prices: dict,
    settings: Any,
    stop_event: threading.Event,
) -> str | None:
    """
    ШАГ 4: посекундный delay, затем ETH transfer на субаккаунт биржи.
    Возвращает tx_hash или None при ошибке/пропуске.
    """
    loop = asyncio.get_running_loop()

    logger.info(
        "[Wallet %s] Waiting %ds before exchange transfer...",
        address[:10], settings.delay_after_bridge
    )
    # Посекундный цикл — Stop сработает немедленно
    for _ in range(settings.delay_after_bridge):
        if stop_event.is_set():
            return None
        await asyncio.sleep(1)

    try:
        w3 = rpc_resolver.get_web3(tgt_chain_id)
        balance_wei = await loop.run_in_executor(None, w3.eth.get_balance, address)

        # Расчёт газа
        chain_gas = gas_prices.get(str(tgt_chain_id), gas_prices.get(tgt_chain_id, {}))
        gas_price_wei = chain_gas.get("fast") or chain_gas.get("standard") or 0
        if not gas_price_wei:
            gas_price_wei = await loop.run_in_executor(None, lambda: w3.eth.gas_price)

        tx_stub = {"from": address, "to": exchange_address, "value": balance_wei}
        estimated_gas = await loop.run_in_executor(None, w3.eth.estimate_gas, tx_stub)
        gas_cost_wei = estimated_gas * gas_price_wei
        send_amount = balance_wei - (gas_cost_wei * 2)

        if send_amount <= 0:
            logger.info("[Wallet %s] Balance too low for exchange transfer", address[:10])
            return None

        # Строим Legacy-транзакцию для простого ETH transfer
        nonce = await loop.run_in_executor(
            None, lambda: w3.eth.get_transaction_count(address, "pending")
        )
        chain_id = await loop.run_in_executor(None, lambda: w3.eth.chain_id)
        tx_req = {
            "to": exchange_address,
            "data": "0x",
            "value": hex(send_amount),
            "gasLimit": hex(estimated_gas),
            "gasPrice": hex(gas_price_wei),
            "chainId": chain_id,
        }

        from app.modules.token_collector._signer import sign_and_send
        tx_hash, receipt = await loop.run_in_executor(
            None, sign_and_send, w3, tx_req, private_key, address
        )

        if receipt is None:
            return None

        native_symbol = "ETH"
        logger.info(
            "[Wallet %s] Exchange transfer: %.6f %s → %s | tx: %s",
            address[:10], send_amount / 1e18, native_symbol, exchange_address, tx_hash
        )
        return tx_hash

    except Exception as e:
        logger.error("[Wallet %s] Exchange transfer error: %s", address[:10], e)
        return None
