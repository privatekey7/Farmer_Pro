# app/modules/token_collector/_bridge_logic.py
from __future__ import annotations
import asyncio
import logging
import random
import threading
import time
from typing import Any

from app.core.logger import SUCCESS

logger = logging.getLogger(__name__)

# Таймаут ожидания бриджа — 30 минут
BRIDGE_TIMEOUT_SEC = 30 * 60

# Перевод на биржу: попыток подготовки (баланс, газ) при сбое RPC и пауза между ними.
EXCHANGE_PREP_ATTEMPTS = 3
EXCHANGE_RETRY_SLEEP = 2.0

# Бридж только если отправляемая сумма ≥ BRIDGE_MIN_VALUE_RATIO × ожидаемой
# комиссии. Раньше с eth ушло $0.022 при газе $0.077.
BRIDGE_MIN_VALUE_RATIO = 3

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


def _bridge_gas_reserve(w3: Any, tx_req: dict, l1_fee: int) -> int | None:
    costs = _bridge_gas_costs(w3, tx_req, l1_fee)
    return costs[0] if costs else None


def _pick_provider(balance_wei: int, options: dict[str, tuple[int, int]]) -> str:
    """Провайдер с наибольшим ЧИСТЫМ результатом: out × (баланс − запас на газ) / баланс.

    Пробные котировки считаются на весь баланс, а газа провайдеры закладывают
    по-разному: на eth LI.FI — 536 900 gas, Relay — 32 713; на era — 4 033 000
    против 272 344. Сравнение только по out выбирало LI.FI, и его запас съедал
    почти весь баланс (с eth ушло $0.022 из $0.44, на era остался $1).
    options: {provider: (out_probe, gas_reserve)}.
    """
    def net(item: tuple[str, tuple[int, int]]) -> tuple[int, int]:
        out, reserve = item[1]
        sendable = max(0, balance_wei - reserve)
        return (out * sendable // balance_wei if balance_wei > 0 else 0, out)

    return max(options.items(), key=net)[0]


def _bridge_gas_costs(w3: Any, tx_req: dict, l1_fee: int) -> tuple[int, int] | None:
    """(запас на газ, ожидаемая комиссия) бриджа.

    Запас: gasLimit × цена, которую поставит sign_and_send, + L1, +5%.

    Раньше: 2 × gasLimit × maxFeePerGas из котировки. На zkSync gasLimit и
    потолок котировки завышены многократно: запас выходил $1.40 при реальной
    стоимости ~$0.03, и $1.41 так и оставались на era. Цена здесь — та же,
    что поставит sign_and_send (baseFee × 1.1 + priority; legacy — max(gasPrice,
    2×baseFee)), а race между проверкой и отправкой закрывает _fit_max_fee.
    Ожидаемая комиссия (для правила выгоды): gasLimit × (baseFee + priority) + L1 —
    без потолка, это приблизительно то, что реально спишется.
    None — посчитать нельзя (нет gasLimit/блока), вызывающий берёт старую оценку.
    """
    from app.modules.token_collector._signer import MIN_MAX_FEE_PCT, effective_priority

    gas_limit = _hi(tx_req.get("gasLimit") or tx_req.get("gas"))
    if not gas_limit:
        return None
    try:
        base_fee = w3.eth.get_block("latest").get("baseFeePerGas") or 0
    except Exception:
        return None
    if "maxFeePerGas" in tx_req:
        if not base_fee:
            return None
        priority = effective_priority(w3, _hi(tx_req.get("maxPriorityFeePerGas")))
        price = base_fee * MIN_MAX_FEE_PCT // 100 + priority
        expected_price = base_fee + priority
    else:
        quote_gp = _hi(tx_req.get("gasPrice"))
        price = max(quote_gp, base_fee * 2) if base_fee else quote_gp * 2
        expected_price = price          # legacy: платится вся gasPrice
    if not price:
        return None
    return (gas_limit * price + l1_fee) * 105 // 100, gas_limit * expected_price + l1_fee


def _relay_step_txs(relay_q: dict) -> list[dict]:
    """Все транзакции котировки Relay по порядку: подготовительные (approve…) и депозит.

    Для сетей, где «нативный» токен — ERC-20 (Celo), Relay отдаёт два шага:
    approve + deposit. Раньше брался только steps[0] — уходил один approve,
    депозит не отправлялся, а приложение 30 минут ждало поступления.
    Шаги-подписи (kind != transaction) не поддерживаются → исключение (бридж не делается).
    """
    txs: list[dict] = []
    for step in relay_q.get("steps") or []:
        kind = step.get("kind", "transaction")
        if kind != "transaction":
            raise RuntimeError(f"Relay step '{step.get('id')}' kind={kind} is not supported")
        for item in step.get("items") or []:
            txs.append(item["data"])
    if not txs:
        raise RuntimeError("Relay quote has no transactions")
    return txs


def _sum_gas_costs(w3: Any, txs: list[dict], l1_fees: list[int]) -> tuple[int, int] | None:
    """(запас, ожидаемая комиссия) для последовательности транзакций; None — если хоть одну не оценить."""
    reserve = expected = 0
    for tx, l1 in zip(txs, l1_fees):
        c = _bridge_gas_costs(w3, tx, l1)
        if c is None:
            return None
        reserve += c[0]
        expected += c[1]
    return reserve, expected


async def _send_tx_sequence(
    loop: asyncio.AbstractEventLoop, w3: Any, txs: list[dict], private_key: str, address: str,
    ops: list[dict] | None, chain_label: str,
) -> tuple[str | None, str | None]:
    """Отправляет шаги по порядку. Все, кроме последнего, — подготовительные (approve):
    ждём подтверждения, иначе основную tx не шлём (без allowance она откатится).
    Последняя — основная (весь баланс минус газ, потолок ×1.1).

    Возвращает (хэш последней отправленной tx, статус ошибки | None при успехе).
    """
    from app.modules.token_collector._signer import SEND_ALL_MAX_FEE_PCT, TransactionReverted, sign_and_send

    for i, tx in enumerate(txs):
        last = i == len(txs) - 1
        kwargs = {"max_fee_pct": SEND_ALL_MAX_FEE_PCT} if last else {}
        try:
            tx_hash, receipt = await loop.run_in_executor(
                None, lambda tx=tx, kwargs=kwargs: sign_and_send(w3, tx, private_key, address, **kwargs)
            )
        except TransactionReverted as e:
            if not last and ops is not None:
                ops.append({"type": "approve", "chain": chain_label, "detail": "relay step",
                            "usd": 0.0, "tx": e.tx_hash, "status": "REVERTED"})
            logger.warning("[Wallet %s] Tx reverted (step %d/%d): %s", address[:10], i + 1, len(txs), e.tx_hash)
            return e.tx_hash, "TX_REVERTED"
        except Exception as e:
            if "-32000" in str(e):
                logger.warning("[Wallet %s] Tx rejected by node (step %d/%d): %s",
                               address[:10], i + 1, len(txs), str(e)[:160])
                return None, "NODE_REJECTED"
            raise
        if last:
            return tx_hash, None
        if ops is not None:
            ops.append({"type": "approve", "chain": chain_label, "detail": "relay step",
                        "usd": 0.0, "tx": tx_hash, "status": "CONFIRMED" if receipt is not None else "PENDING"})
        if receipt is None:
            logger.warning("[Wallet %s] Step %d/%d not confirmed — next step not sent: %s",
                           address[:10], i + 1, len(txs), tx_hash)
            return tx_hash, "TIMEOUT"
        logger.log(SUCCESS, "[Wallet %s] Step %d/%d confirmed (approve): %s", address[:10], i + 1, len(txs), tx_hash)
    return None, "NO_QUOTE"


def _relay_quote_is_too_small_error(exc: Exception) -> bool:
    """Определяет ошибки Relay, где нужно просто увеличить input-amount."""
    msg = str(exc).lower()
    needles = (
        "too small",
        "cover fees",
        "minimum amount",
        "minimum input",
        "insufficient output amount",
    )
    return any(needle in msg for needle in needles)


def _relay_quote_out_amount(relay_quote: dict) -> int:
    """Возвращает ожидаемый amount на destination chain из Relay quote."""
    try:
        return int(relay_quote.get("details", {}).get("currencyOut", {}).get("amount") or 0)
    except Exception:
        return 0


async def _find_refuel_quote(
    *,
    loop: asyncio.AbstractEventLoop,
    relay_client: Any,
    donor_chain_id: int,
    tgt_chain_id: int,
    donor_native: str,
    tgt_native: str,
    address: str,
    requested_amount_wei: int,
    min_required_out_wei: int,
    max_refuel_amount_wei: int | None,
) -> tuple[int, int, dict] | tuple[None, None, None]:
    """
    Подбирает минимальный input для Relay refuel.
    Увеличивает сумму, если route слишком маленький или ожидаемый output меньше требуемого.
    """
    candidate_amount = max(1, int(requested_amount_wei))
    max_amount = max(candidate_amount, int(max_refuel_amount_wei or candidate_amount))
    last_error: Exception | None = None

    for _ in range(8):
        try:
            relay_q = await loop.run_in_executor(
                None,
                relay_client.get_quote,
                donor_chain_id, tgt_chain_id,
                donor_native, tgt_native,
                str(candidate_amount), address,
            )
            out_amount = _relay_quote_out_amount(relay_q)
            if min_required_out_wei and out_amount < min_required_out_wei:
                last_error = RuntimeError(
                    f"quoted output {out_amount} < required {min_required_out_wei}"
                )
            else:
                return candidate_amount, out_amount, relay_q
        except Exception as e:
            last_error = e
            if not _relay_quote_is_too_small_error(e):
                break
            out_amount = 0

        if candidate_amount >= max_amount:
            break

        if min_required_out_wei and out_amount > 0:
            scaled_amount = (
                candidate_amount * min_required_out_wei * 11 + (out_amount * 10) - 1
            ) // (out_amount * 10)
            next_amount = max(candidate_amount * 2, scaled_amount)
        else:
            next_amount = candidate_amount * 2

        next_amount = min(max_amount, next_amount)
        if next_amount <= candidate_amount:
            break

        logger.info(
            "[Refuel] Bumping input for %s → %s from %d to %d wei",
            donor_chain_id, tgt_chain_id, candidate_amount, next_amount,
        )
        candidate_amount = next_amount

    if last_error:
        logger.warning(
            "[Refuel] Could not find viable quote %s → %s up to %d wei: %s",
            donor_chain_id, tgt_chain_id, max_amount, last_error,
        )
    return None, None, None


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
    tgt_chain_id: int,
    src_chain_id: int,
    gas_prices: dict,
    stop_event: threading.Event,
    ops: list[dict] | None = None,
) -> tuple[str | None, str, int | None, float]:
    """
    ШАГ 3: бридж нативного токена из src_chain_id в tgt_chain_id.
    Возвращает (bridge_tx, bridge_status, tgt_chain_id, sent_usd).
    sent_usd — USD-стоимость отправленной суммы при статусе COMPLETED, иначе 0.0.

    tgt_chain_id выбирается ОДИН раз на кошелёк (вызывающим): раньше здесь
    был random.choice на каждую исходную сеть — средства расходились по разным
    целевым сетям, а на биржу уходили только из одной.
    """
    from app.integrations.lifi_client import LiFiNoRouteError
    from app.integrations.relay_client import RelayNoRouteError
    from app.modules.token_collector._signer import SEND_ALL_MAX_FEE_PCT, sign_and_send, TransactionReverted

    loop = asyncio.get_running_loop()

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
    lifi_tx_req_probe: dict = {}
    relay_out = 0
    relay_gas = 0
    relay_l1_fee = 0
    relay_tx_req: dict = {}
    relay_txs: list[dict] = []
    relay_l1_fees: list[int] = []

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
            relay_txs = _relay_step_txs(relay_q)
            relay_tx_req = relay_txs[-1]                      # депозит
            relay_gas = sum(_tx_gas_cost(t) for t in relay_txs)
            if relay_gas == 0:
                relay_gas = int(relay_q["fees"]["gas"]["amount"])
            relay_out = int(relay_q["details"]["currencyOut"]["amount"])
            # L1 data fee для OP-stack (на ETH/ARB/etc. возвращает 0) — по каждому шагу
            relay_l1_fees = [await loop.run_in_executor(None, _get_l1_fee_safe, w3, t) for t in relay_txs]
            relay_l1_fee = sum(relay_l1_fees)
        except Exception as e:
            logger.warning("[Wallet %s] Relay quote failed: %s", address[:10], e)
            relay_ok = False

    if not lifi_ok and not relay_ok:
        return None, "NO_QUOTE", None, 0.0

    # Запас на газ и ожидаемая комиссия — у каждого провайдера свои
    # (нет данных для точной оценки — прежняя консервативная: газ котировки × 2 + L1).
    costs: dict[str, tuple[int, int | None]] = {}
    if lifi_ok:
        c = await loop.run_in_executor(None, _bridge_gas_costs, w3, lifi_tx_req_probe, lifi_l1_fee)
        costs["lifi"] = c if c else (lifi_gas * 2 + lifi_l1_fee, None)
    if relay_ok:
        c = await loop.run_in_executor(None, _sum_gas_costs, w3, relay_txs, relay_l1_fees)
        costs["relay"] = c if c else (relay_gas * 2 + relay_l1_fee, None)
    outs = {"lifi": lifi_out, "relay": relay_out}

    # Выбор провайдера по чистому результату (после запаса на газ), а не по out на весь баланс
    provider = _pick_provider(balance_wei, {p: (outs[p], costs[p][0]) for p in costs})

    chosen_gas = lifi_gas if provider == "lifi" else relay_gas
    chosen_l1_fee = lifi_l1_fee if provider == "lifi" else relay_l1_fee
    chosen_out = outs[provider]
    gas_reserve, expected_fee = costs[provider]
    logger.info(
        "[Bridge] provider=%s out=%.6f | lifi out=%s reserve=%s | relay out=%s reserve=%s%s",
        provider, chosen_out / 1e18,
        lifi_out, costs.get("lifi", (None,))[0], relay_out, costs.get("relay", (None,))[0],
        f" | l1_fee={chosen_l1_fee}" if chosen_l1_fee else "",
    )

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

    # Правило выгоды: не отправлять сумму, сопоставимую с комиссией
    if expected_fee and send_amount < BRIDGE_MIN_VALUE_RATIO * expected_fee:
        logger.info(
            "[Wallet %s] Bridge skipped: send $%.4f < %d × expected gas $%.4f — not worth it",
            address[:10], send_amount / 1e18 * price_usd, BRIDGE_MIN_VALUE_RATIO,
            expected_fee / 1e18 * price_usd,
        )
        return None, "NOT_WORTH", tgt_chain_id, 0.0

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
            final_txs = [final_quote["transactionRequest"]]
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
            final_txs = _relay_step_txs(final_quote)
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

    # Все шаги (approve + deposit у Relay на Celo): газ, L1 и value — суммой
    from app.modules.token_collector._signer import effective_priority
    final_gas_cost = l1_data_fee = tx_value = gas_limit = 0
    effective_gas_price = 0
    for tx_req in final_txs:
        step_gas = _hi(tx_req.get("gasLimit") or tx_req.get("gas"))
        if "maxFeePerGas" in tx_req:
            priority = await loop.run_in_executor(
                None, effective_priority, w3, _hi(tx_req.get("maxPriorityFeePerGas"))
            )
            effective_gas_price = base_fee * 11 // 10 + priority
        else:
            quote_gp = _hi(tx_req.get("gasPrice"))
            effective_gas_price = max(quote_gp, base_fee * 2) if base_fee else quote_gp * 2
        gas_limit += step_gas
        final_gas_cost += step_gas * effective_gas_price
        l1_data_fee += await loop.run_in_executor(None, _get_l1_fee_safe, w3, tx_req)
        tx_value += _hi(tx_req.get("value"))
    logger.info(
        "[Wallet %s] Safety check: steps=%d value=%d gas_limit=%d gas_price=%d gas_cost=%d l1_fee=%d balance=%d",
        address[:10], len(final_txs), tx_value, gas_limit, effective_gas_price, final_gas_cost, l1_data_fee,
        balance_wei,
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

    tx_hash, fail_status = await _send_tx_sequence(
        loop, w3, final_txs, private_key, address, ops, chain_label=str(src_chain_id),
    )
    if fail_status:
        return tx_hash, fail_status, tgt_chain_id, 0.0

    # Ожидаемый минимум поступления (50% от quoted toAmountMin — консервативный порог).
    # Обе ветки — по ФИНАЛЬНОЙ котировке (по ней ушла tx); у Relay раньше бралась
    # пробная котировка на весь баланс, а не на send_amount.
    if provider == "lifi":
        to_amount_min = int(final_quote.get("estimate", {}).get("toAmountMin", 0))
    else:
        to_amount_min = int(final_quote.get("details", {}).get("currencyOut", {}).get("minimumAmount", 0) or 0)
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
                logger.log(
                    SUCCESS,
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
    min_required_out_wei: int,
    max_refuel_amount_wei: int | None,
    relay_client: Any,
    relay_native_by_id: dict[int, dict],
    rpc_resolver: Any,
    stop_event: threading.Event,
    ops: list[dict] | None = None,
    donor_price_usd: float = 0.0,
    route_label: str = "",
) -> bool:
    """
    ШАГ 2.5: отправляет небольшое количество нативного токена из donor_chain_id
    в tgt_chain_id через Relay, чтобы покрыть газ для последующих свапов.
    Возвращает True если средства успешно доставлены.
    ops (если передан) пополняется записью об отправленном refuel: сумма в USD
    по donor_price_usd, хэш и статус (SENT → ARRIVED / TIMEOUT).
    """
    from app.modules.token_collector._signer import SEND_ALL_MAX_FEE_PCT, TransactionReverted, sign_and_send

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
        "[Refuel] %s → chain %s: requesting %d wei (%s ETH) for gas",
        donor_chain_id, tgt_chain_id, refuel_amount_wei, refuel_amount_wei / 1e18,
    )

    actual_refuel_amount, quoted_out_amount, relay_q = await _find_refuel_quote(
        loop=loop,
        relay_client=relay_client,
        donor_chain_id=donor_chain_id,
        tgt_chain_id=tgt_chain_id,
        donor_native=donor_native,
        tgt_native=tgt_native,
        address=address,
        requested_amount_wei=refuel_amount_wei,
        min_required_out_wei=min_required_out_wei,
        max_refuel_amount_wei=max_refuel_amount_wei,
    )
    if not relay_q or actual_refuel_amount is None:
        return False

    try:
        refuel_txs = _relay_step_txs(relay_q)
    except Exception as e:
        logger.warning("[Refuel] Unsupported Relay quote %s → %s: %s", donor_chain_id, tgt_chain_id, e)
        return False
    if actual_refuel_amount != refuel_amount_wei:
        logger.info(
            "[Refuel] Adjusted input %s → %s: %d -> %d wei (quoted out %d wei)",
            donor_chain_id, tgt_chain_id, refuel_amount_wei, actual_refuel_amount, quoted_out_amount or 0,
        )
    else:
        logger.info(
            "[Refuel] %s → chain %s: sending %d wei (%s ETH) for gas",
            donor_chain_id, tgt_chain_id, actual_refuel_amount, actual_refuel_amount / 1e18,
        )

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

    label = route_label or f"{donor_chain_id} → {tgt_chain_id}"
    try:
        tx_hash, fail_status = await _send_tx_sequence(
            loop, w3_donor, refuel_txs, private_key, address, ops, chain_label=label,
        )
    except Exception as e:
        logger.warning("[Refuel] Failed to send refuel tx: %s", e)
        return False
    if fail_status:
        # refuel, откатившийся on-chain, тоже попадает в список операций
        if ops is not None and tx_hash and fail_status == "TX_REVERTED" and len(refuel_txs) == 1:
            ops.append({"type": "refuel", "chain": label, "detail": "gas", "usd": 0.0,
                        "tx": tx_hash, "status": "REVERTED"})
        logger.warning("[Refuel] Refuel not sent/failed (%s): %s", fail_status, tx_hash)
        return False

    op = {"type": "refuel", "chain": route_label or f"{donor_chain_id} → {tgt_chain_id}", "detail": "gas",
          "usd": round(actual_refuel_amount / 1e18 * donor_price_usd, 4), "tx": tx_hash, "status": "SENT"}
    if ops is not None:
        ops.append(op)

    logger.info("[Refuel] Tx sent: %s — waiting for arrival on chain %s...", tx_hash, tgt_chain_id)

    # Ожидаем поступления
    expected_base = quoted_out_amount or actual_refuel_amount
    expected_min = max(1, int(expected_base * 0.3))  # 30% от ожидаемого output
    elapsed = 0
    while elapsed < REFUEL_TIMEOUT_SEC:
        if stop_event.is_set():
            return False
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        try:
            balance = await loop.run_in_executor(None, w3_tgt.eth.get_balance, address)
            if balance - pre_balance >= expected_min:
                op["status"] = "ARRIVED"
                logger.log(
                    SUCCESS,
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

    op["status"] = "TIMEOUT"
    logger.warning("[Refuel] Timeout waiting for arrival on chain %s", tgt_chain_id)
    return False


def exchange_pct(settings: Any) -> int:
    """Процент доступного баланса для перевода на биржу — целый, 1–100.

    exchange_pct_min == exchange_pct_max → фиксированный процент; иначе —
    случайный целый в диапазоне (для каждого кошелька свой). Значения вне 1–100
    обрезаются, перепутанные «от»/«до» меняются местами. Нет настроек → 100%.
    """
    lo = int(getattr(settings, "exchange_pct_min", 100) or 100)
    hi = int(getattr(settings, "exchange_pct_max", 100) or 100)
    lo, hi = sorted((min(100, max(1, lo)), min(100, max(1, hi))))
    return random.randint(lo, hi)


async def send_to_exchange(
    address: str,
    private_key: str,
    exchange_address: str,
    tgt_chain_id: int,
    rpc_resolver: Any,
    gas_prices: dict,
    settings: Any,
    stop_event: threading.Event,
    ops: list[dict] | None = None,
    price_usd: float = 0.0,
) -> str | None:
    """
    ШАГ 4: посекундный delay, затем ETH transfer на субаккаунт биржи.
    Возвращает tx_hash (в т.ч. если tx ушла, но не подтвердилась) или None при
    ошибке/пропуске. ops (если передан) пополняется записью о переводе.
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
        from web3 import Web3
        from app.modules.token_collector._signer import SWAP_MAX_FEE_PCT, sign_and_send

        # Адрес из файла субаккаунтов может быть в нижнем регистре: подпись его
        # нормализовала, а estimate_gas — нет, и перевод падал
        # («web3.py only accepts checksum addresses»).
        exchange_address = Web3.to_checksum_address(exchange_address)
        w3 = rpc_resolver.get_web3(tgt_chain_id)
        # Подготовка — с повтором через другой RPC: соединение, простоявшее минуты
        # (пауза после бриджа), нода может закрыть — «Connection aborted / 10054».
        # Отправку не повторяем здесь: её обрывы обрабатывает sign_and_send.
        for attempt in range(EXCHANGE_PREP_ATTEMPTS):
            try:
                prepared = await _prepare_exchange(loop, w3, address, exchange_address, tgt_chain_id, gas_prices, settings)
                break
            except Exception as e:
                if attempt == EXCHANGE_PREP_ATTEMPTS - 1:
                    raise
                logger.warning("[Wallet %s] RPC error preparing exchange transfer (%s) — retrying via another RPC",
                               address[:10], str(e)[:100])
                await asyncio.sleep(EXCHANGE_RETRY_SLEEP)
                try:
                    w3 = rpc_resolver.rotate(tgt_chain_id)
                except Exception:
                    pass
        if prepared is None:
            return None
        tx_req, send_amount, pct = prepared

        tx_hash, receipt = await loop.run_in_executor(
            None, lambda: sign_and_send(w3, tx_req, private_key, address, keep_fees=True)
        )

        op = {"type": "exchange", "chain": str(tgt_chain_id), "detail": f"{exchange_address} ({pct}%)",
              "usd": round(send_amount / 1e18 * price_usd, 4), "tx": tx_hash,
              "status": "CONFIRMED" if receipt is not None else "PENDING"}
        if ops is not None:
            ops.append(op)

        if receipt is None:
            logger.warning("[Wallet %s] Exchange transfer not confirmed yet: %s", address[:10], tx_hash)
            return tx_hash

        native_symbol = "ETH"
        logger.log(
            SUCCESS,
            "[Wallet %s] Exchange transfer: %.6f %s (%d%% of available) → %s | tx: %s",
            address[:10], send_amount / 1e18, native_symbol, pct, exchange_address, tx_hash
        )
        return tx_hash

    except Exception as e:
        logger.error("[Wallet %s] Exchange transfer error: %s", address[:10], e)
        if ops is not None:
            ops.append({"type": "exchange", "chain": str(tgt_chain_id), "detail": f"{exchange_address}: {str(e)[:120]}",
                        "usd": 0.0, "tx": "", "status": "FAILED"})
        return None


async def _prepare_exchange(
    loop: asyncio.AbstractEventLoop, w3: Any, address: str, exchange_address: str,
    tgt_chain_id: int, gas_prices: dict, settings: Any,
) -> tuple[dict, int, int] | None:
    """Готовит перевод на биржу: (tx_req, сумма, процент) или None — отправлять нечего."""
    from app.modules.token_collector._signer import SWAP_MAX_FEE_PCT

    balance_wei = await loop.run_in_executor(None, w3.eth.get_balance, address)

    tx_stub = {"from": address, "to": exchange_address, "value": balance_wei}
    estimated_gas = await loop.run_in_executor(None, w3.eth.estimate_gas, tx_stub)
    chain_id = await loop.run_in_executor(None, lambda: w3.eth.chain_id)
    # L1 data fee (OP-stack): списывается сверх gas × price — резервируем явно.
    l1_fee = await loop.run_in_executor(None, _get_l1_fee_safe, w3, {"data": "0x"})

    # Комиссии считаются ОДИН раз и уходят в tx как есть (keep_fees=True).
    # Раньше резерв считался по «fast»-цене LI.FI, а sign_and_send потом
    # поднимал gasPrice до 2×baseFee — value + gas превышали баланс, нода
    # отвечала «insufficient funds», и перевод молча не выполнялся.
    base_fee = None
    try:
        block = await loop.run_in_executor(None, lambda: w3.eth.get_block("latest"))
        base_fee = block.get("baseFeePerGas")
    except Exception as e:
        logger.debug("[Wallet %s] get_block failed for exchange transfer: %s", address[:10], e)

    if base_fee is not None:
        # EIP-1559: потолок 2×baseFee, платится фактический baseFee.
        try:
            priority = int(await loop.run_in_executor(None, lambda: w3.eth.max_priority_fee))
        except Exception:
            priority = 1
        priority = max(1, priority)
        max_fee = base_fee * SWAP_MAX_FEE_PCT // 100 + priority
        fee_fields = {"maxFeePerGas": hex(max_fee), "maxPriorityFeePerGas": hex(priority)}
        gas_cost_wei = estimated_gas * max_fee
    else:
        # Legacy-сеть: цена LI.FI «fast» (или сети) +20% запаса.
        chain_gas = gas_prices.get(str(tgt_chain_id), gas_prices.get(tgt_chain_id, {}))
        gas_price_wei = chain_gas.get("fast") or chain_gas.get("standard") or 0
        if not gas_price_wei:
            gas_price_wei = await loop.run_in_executor(None, lambda: w3.eth.gas_price)
        gas_price_wei = int(gas_price_wei) * 12 // 10
        fee_fields = {"gasPrice": hex(gas_price_wei)}
        gas_cost_wei = estimated_gas * gas_price_wei

    available = balance_wei - gas_cost_wei - l1_fee
    if available <= 0:
        logger.info("[Wallet %s] Balance too low for exchange transfer", address[:10])
        return None

    # Процент от доступного (баланс минус газ перевода): 100% — всё, как раньше
    pct = exchange_pct(settings)
    send_amount = available * pct // 100
    if send_amount <= 0:
        logger.info("[Wallet %s] Exchange amount is 0 at %d%%", address[:10], pct)
        return None

    tx_req = {
        "to": exchange_address,
        "data": "0x",
        "value": hex(send_amount),
        "gasLimit": hex(estimated_gas),
        "chainId": chain_id,
        **fee_fields,
    }
    return tx_req, send_amount, pct
