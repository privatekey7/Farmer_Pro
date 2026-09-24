# app/modules/token_collector/__init__.py
from __future__ import annotations
import asyncio
import logging
import random
import threading
from typing import AsyncIterator

from PySide6.QtCore import QObject, Signal

from app.core.base_module import BaseModule
from app.core.logger import SUCCESS
from app.core.models import RunContext, Result, ResultStatus, ColumnDef
from app.integrations.lifi_client import (
    LiFiClient, DEBANK_TO_CHAIN_ID, LiFiChainRegistry,
)
from app.integrations.relay_client import RelayClient
from app.integrations.rpc_resolver import RpcResolver
from app.integrations.proxy_utils import ProxyRotator, is_proxy_alive
from app.integrations.chainlist_client import fetch_chainlist_rpcs
from app.modules.token_collector._collector_logic import fetch_and_swap, retry_gasless_swaps
from app.modules.token_collector._bridge_logic import bridge_native, send_to_exchange, refuel_chain
from app.modules.token_collector._signer import derive_address

logger = logging.getLogger(__name__)

# Refuel только если токены на сети стоят не меньше REFUEL_MIN_VALUE_RATIO ×
# оценки газа их свопа. Раньше refuel шёл без проверки: $3.89 переводились
# ради токена за $0.03, $2.56 — ради двух токенов по $0.26, которые потом
# откатились.
REFUEL_MIN_VALUE_RATIO = 3

# Статусы бриджа, при которых транзакция реально уходила в сеть (или нода её
# отклонила при отправке) — это «попытка». BELOW_MIN / INSUFFICIENT / NO_QUOTE /
# NO_ROUTE / NO_RPC — бридж не пытался отправляться (пыль, нет маршрута).
# Реальная попытка бриджа, которая не удалась (в отличие от пропуска).
_BRIDGE_FAILED_STATUSES = ("NODE_REJECTED", "TX_REVERTED", "TIMEOUT", "REFUNDED")

_BRIDGE_ATTEMPT_STATUSES = (
    "COMPLETED", "SENT", "PARTIAL", "REFUNDED", "TIMEOUT", "TX_REVERTED", "NODE_REJECTED", "STOPPED",
)


def _wallet_status(
    bridge_ops: list[dict], ops: list[dict], swaps_ok: int, exchange_tx: str | None, exchange_failed: bool,
) -> tuple[ResultStatus, str | None]:
    """Итоговый статус кошелька — по тому, что реально произошло (одна функция на все пути).

    ERROR — упал перевод на биржу, либо были только неудачные попытки (бридж/своп);
    OK    — прошёл хотя бы один бридж, своп или перевод на биржу;
    SKIP  — делать было нечего: пыль, нет маршрута, нет RPC у сети с пылью.
    Раньше статус брался по «лучшему» статусу бриджа, а свопы не учитывались.
    """
    if exchange_failed:
        # Перевод на биржу — цель прогона: его провал не может быть «OK»
        return ResultStatus.ERROR, "exchange transfer failed"
    bridged = any(op["status"] in ("COMPLETED", "PARTIAL", "SENT") for op in bridge_ops)
    if bridged or swaps_ok or exchange_tx:
        return ResultStatus.OK, None
    if any(op["status"] in _BRIDGE_FAILED_STATUSES for op in bridge_ops):
        return ResultStatus.ERROR, "bridge failed"
    if any(op["type"] == "swap" and op["status"] == "REVERTED" for op in ops):
        return ResultStatus.ERROR, "swap failed"
    return ResultStatus.SKIP, None


def _pick_working_proxy(rotator: ProxyRotator, alive: dict[str, bool]):
    """Следующий РАБОЧИЙ прокси из ротации (None — рабочих нет).

    Каждый прокси проверяется один раз за прогон (alive — кэш результатов);
    мёртвый исключается из ротации до конца прогона.
    """
    for _ in range(len(rotator)):
        proxy = rotator.next()
        if proxy is None:
            return None
        url = proxy.to_url()
        if url not in alive:
            alive[url] = is_proxy_alive(url)
            if not alive[url]:
                logger.warning("Proxy %s is not working — skipped for this run", url.split("@")[-1])
                rotator.cooldown(url, float("inf"))
        if alive[url]:
            return proxy
    return None


def _build_processed_bridge_keys(chains_processed: str, target_chains: list[str]) -> list[str]:
    """Готовит список source chains для bridge loop без дублей и target chains."""
    target_key_set = {chain.lower() for chain in target_chains}
    seen: set[str] = set()
    result: list[str] = []

    for raw_key in chains_processed.split(", "):
        key = raw_key.strip()
        key_lower = key.lower()
        if not key or key_lower in target_key_set or key_lower in seen:
            continue
        seen.add(key_lower)
        result.append(key)

    return result


class _CollectorSignals(QObject):
    # Создаётся в __init__ (main thread) — обеспечивает Qt thread affinity
    run_complete = Signal(list, dict)


class CollectorModule(BaseModule):
    name = "Collector"

    def column_schema(self) -> list[ColumnDef]:
        return [
            ColumnDef(key="item",                label="Address",       width=200),
            ColumnDef(key="status",              label="Status"),
            # Swaps / Bridges — «успешно/попыток»: попытка = реально отправленная tx.
            # Route и Bridge убраны: показывали один (лучший) бридж из нескольких.
            ColumnDef(key="swaps_summary",       label="Swaps"),
            ColumnDef(key="swapped_usd",         label="Swapped $",     fmt="${:.2f}", sort_type="numeric"),
            ColumnDef(key="bridge_summary",      label="Bridges"),
            ColumnDef(key="total_collected_usd",  label="Bridged $",    fmt="${:.2f}", sort_type="numeric"),
            ColumnDef(key="refuel_usd",          label="Refuel $",      fmt="${:.2f}", sort_type="numeric"),
            ColumnDef(key="target",              label="Target"),
        ]

    def __init__(self) -> None:
        from app.ui.module_views.collector_view import CollectorConfigWidget
        self._signals = _CollectorSignals()
        self._results: list[Result] = []
        self._widget = CollectorConfigWidget()
        self._signals.run_complete.connect(self._widget.on_run_complete)
        self._stop_event = threading.Event()

    def get_config_widget(self):
        return self._widget

    def get_item_count(self) -> int:
        return len(self._widget.get_wallets())

    async def run(self, ctx: RunContext) -> AsyncIterator[Result]:
        self._results.clear()
        self._stop_event.clear()

        wallets = self._widget.get_wallets()
        proxies = self._widget.get_proxies()
        subaccounts = self._widget.get_subaccounts()
        settings = self._widget.get_settings()
        rotator = ProxyRotator(proxies)
        loop = asyncio.get_running_loop()

        # ── ШАГ 0: Валидация ─────────────────────────────────────────────
        if settings.send_to_exchange and len(wallets) != len(subaccounts):
            raise ValueError(
                f"Количество кошельков ({len(wallets)}) не совпадает "
                f"с количеством субаккаунтов ({len(subaccounts)})"
            )

        alive_proxies: dict[str, bool] = {}
        proxy = await loop.run_in_executor(None, _pick_working_proxy, rotator, alive_proxies)
        if proxies and proxy is None:
            raise RuntimeError("Ни один прокси не работает — проверьте файл прокси (без прокси коллектор не работает)")
        proxy_url = proxy.to_url() if proxy else None
        lifi_client = LiFiClient(proxy=proxy_url)
        relay_client = RelayClient(proxy=proxy_url)
        shared_lifi_client = lifi_client       # кэш маршрутов общий; клиенты кошельков — со своим прокси

        # LI.FI /chains
        lifi_chain_ids: set[int] = set()
        lifi_rpc_by_id: dict[int, list[str]] = {}
        native_token_by_id: dict[int, dict] = {}
        lifi_key_by_id: dict[int, str] = {}
        name_by_id: dict[int, str] = {}

        try:
            chains = await loop.run_in_executor(None, lifi_client.get_chains)
            lifi_chain_ids = {c["id"] for c in chains}
            lifi_rpc_by_id = {c["id"]: c.get("metamask", {}).get("rpcUrls", []) for c in chains}
            native_token_by_id = {c["id"]: c["nativeToken"] for c in chains if "nativeToken" in c}
            lifi_key_by_id = {c["id"]: c.get("key", "") for c in chains}
            name_by_id = {c["id"]: c.get("name", str(c["id"])) for c in chains}
            logger.info("LI.FI: %d EVM chains loaded", len(lifi_chain_ids))
        except Exception as e:
            logger.warning("WARNING: LI.FI /chains failed, using hardcoded chain list. Swaps may be unavailable. (%s)", e)
            lifi_chain_ids = set(DEBANK_TO_CHAIN_ID.values())
            lifi_rpc_by_id = {}
            native_token_by_id = {}

        # Relay /chains
        relay_chain_ids: set[int] = set()
        relay_rpc_by_id: dict[int, str] = {}
        relay_native_by_id: dict[int, dict] = {}

        try:
            relay_chains = await loop.run_in_executor(None, relay_client.get_chains)
            logger.info("Relay: %d chains raw from API", len(relay_chains))
            relay_chain_ids = {
                c["id"] for c in relay_chains
                if c.get("vmType", "evm") == "evm"
                and not c.get("disabled", False)
                and c.get("depositEnabled", True)
            }
            relay_rpc_by_id = {
                c["id"]: c["httpRpcUrl"]
                for c in relay_chains if c.get("httpRpcUrl")
            }
            relay_native_by_id = {c["id"]: c.get("currency", {}) for c in relay_chains}
            logger.info("Relay: %d EVM chains loaded", len(relay_chain_ids))
        except Exception as e:
            logger.warning("WARNING: Relay /chains failed, continuing without Relay provider. (%s)", e)

        supported_chain_ids = lifi_chain_ids | relay_chain_ids
        if not supported_chain_ids:
            raise RuntimeError("No bridge providers available")

        # ChainList RPCs — fallback третий источник (некритично)
        chainlist_rpcs: dict[int, list[str]] = {}
        try:
            chainlist_rpcs = await loop.run_in_executor(None, fetch_chainlist_rpcs)
        except Exception as e:
            logger.warning("ChainList RPCs unavailable, continuing without: %s", e)

        shared_rpc_resolver = RpcResolver(lifi_rpc_by_id, relay_rpc_by_id, chainlist_rpcs)

        # LI.FI /tools (некритично)
        try:
            tools = await loop.run_in_executor(None, lifi_client.get_tools)
            bridges = tools.get("bridges", [])
            exchanges = tools.get("exchanges", [])
            logger.info("LI.FI: %d bridges, %d exchanges loaded", len(bridges), len(exchanges))
        except Exception:
            pass

        # LI.FI /gas/prices
        gas_prices: dict = {}
        try:
            gas_prices = await loop.run_in_executor(None, lifi_client.get_gas_prices)
        except Exception as e:
            logger.warning("WARNING: LI.FI /gas/prices failed, will use web3.eth.gas_price. (%s)", e)

        # Резолвинг target_chains → chain IDs
        ui_logger = ctx.extra.get("logger")
        target_chain_ids: list[int] = []
        for tc in settings.target_chains:
            chain_id = DEBANK_TO_CHAIN_ID.get(tc.lower())
            if chain_id is None:
                # Пробуем по имени сети (case-insensitive)
                for cid, cname in name_by_id.items():
                    if cname.lower() == tc.lower():
                        chain_id = cid
                        break
            if chain_id and chain_id in supported_chain_ids:
                target_chain_ids.append(chain_id)
            else:
                msg = (
                    f"Target chain '{tc}' not found in DEBANK_TO_CHAIN_ID"
                    if chain_id is None
                    else f"Target chain '{tc}' (id={chain_id}) not supported by LI.FI or Relay"
                )
                logger.warning(msg)
                if ui_logger:
                    ui_logger.warning(f"WARNING: {msg}")

        if not target_chain_ids:
            if not settings.target_chains:
                raise ValueError("No target chains selected — please pick at least one chain in Bridge settings")
            raise ValueError(
                f"No valid target chains configured. Selected: {settings.target_chains}. "
                f"None are supported by LI.FI or Relay. Check warnings above."
            )

        # ── ШАГ 1–4: обработка кошельков ─────────────────────────────────
        # parallel_wallets воркеров берут кошельки из общей очереди. Паузы
        # (после свопов/бриджей и между кошельками) — внутри каждого воркера,
        # поэтому при 1 воркере поведение то же, что было последовательно.
        parallel = max(1, min(int(getattr(settings, "parallel_wallets", 1) or 1), len(wallets) or 1))
        logger.info(
            "Starting Collector: %d wallets (%d at a time), target chains: %s",
            len(wallets), parallel, settings.target_chains,
        )

        async def _sleep_interruptible(seconds: int) -> None:
            for _ in range(seconds):
                if self._stop_event.is_set():
                    break
                await asyncio.sleep(1)

        async def process_wallet(wallet_idx: int, wallet: dict) -> tuple[Result, bool]:
            """Обрабатывает один кошелёк. Возвращает (результат, нужна ли пауза после)."""
            logger.info(
                "── Wallet %d/%d ──────────────────────────────",
                wallet_idx + 1, len(wallets),
            )

            # Одна целевая сеть на кошелёк: все бриджи идут в неё, и из неё же
            # уходит перевод на биржу (раньше сеть выбиралась на каждый бридж).
            wallet_tgt_id = random.choice(target_chain_ids)

            # Подпись строки результата — адрес, НЕ начало приватного ключа/мнемоники
            # (раньше при ошибке в таблицу и экспорт попадали первые 42 символа ключа).
            try:
                wallet_label = derive_address(wallet["raw"], wallet["type"])[0]
            except Exception:
                wallet_label = f"wallet #{wallet_idx + 1}"

            # Свой прокси на кошелёк: все запросы к блокчейну и транзакции — через него,
            # не с IP пользователя (блокировки по стране; кошельки не связываются по IP).
            wallet_proxy = await loop.run_in_executor(None, _pick_working_proxy, rotator, alive_proxies)
            if wallet_proxy is None:
                return Result(item=wallet_label, status=ResultStatus.ERROR,
                              error="No working proxies: on-chain actions go only through proxies"), False
            wallet_proxy_url = wallet_proxy.to_url()
            # Все клиенты кошелька — через ОДИН его рабочий прокси: RPC, LI.FI и Relay
            # (раньше LI.FI/Relay шли через первый прокси для всех кошельков, а
            # котировки содержат адрес — сервисы видели все кошельки с одного IP).
            rpc_resolver = shared_rpc_resolver.for_proxy(wallet_proxy_url)
            lifi_client = shared_lifi_client.for_proxy(wallet_proxy_url)
            relay_client = RelayClient(proxy=wallet_proxy_url)

            result_data: dict = {}
            bridge_ops: list[dict] = []
            exchange_tx: str | None = None
            total_sent_usd = 0.0

            try:
                # ШАГ 1-2: балансы (Rabby) + swap
                swap_result = await fetch_and_swap(
                    wallet=wallet,
                    lifi_client=lifi_client,
                    proxy_rotator=rotator,
                    rpc_resolver=rpc_resolver,
                    settings=settings,
                    native_token_by_id=native_token_by_id,
                    relay_native_by_id=relay_native_by_id,
                    lifi_chain_ids=lifi_chain_ids,
                    supported_chain_ids=supported_chain_ids,
                    stop_event=self._stop_event,
                    target_chain_ids=set(target_chain_ids),
                )

                if not swap_result:
                    return Result(
                        item=wallet_label,
                        status=ResultStatus.ERROR,
                        error="fetch_and_swap failed",
                    ), False

                address = swap_result["address"]
                private_key = swap_result["private_key"]
                # Все отправленные транзакции кошелька (approve/swap/refuel/bridge/exchange)
                # — лист Operations в экспорте, 1:1 с историей в Rabby.
                wallet_ops: list[dict] = list(swap_result.pop("ops", []))
                swaps_attempted = int(swap_result.pop("swaps_attempted", 0))
                logger.info(
                    "[Wallet %s] Target chain: %s",
                    address[:10], name_by_id.get(wallet_tgt_id, str(wallet_tgt_id)),
                )

                # ── ШАГ 2.5–2.6: рефьюел и повторный своп на gasless цепях ──
                gasless_chains = swap_result.pop("gasless_chains", [])
                if gasless_chains and not self._stop_event.is_set():
                    target_key_set_local = {k.lower() for k in settings.target_chains}
                    target_cid_set = {DEBANK_TO_CHAIN_ID.get(k) for k in target_key_set_local} | {wallet_tgt_id}
                    # Кандидаты-доноры: только обработанные НЕ-target цепи. Целевая сеть
                    # донором не бывает: собранное туда не должно уходить обратно
                    # (раньше $3.89 ушли с Soneium на era, и $1.41 там застряли).
                    donor_candidates: list[int] = []
                    for k in swap_result.get("chains_processed", "").split(", "):
                        if not k or k.lower() in target_key_set_local:
                            continue
                        cid = DEBANK_TO_CHAIN_ID.get(k)
                        if cid and cid in relay_chain_ids and cid not in target_cid_set:
                            donor_candidates.append(cid)

                    refueled: list[dict] = []
                    for gc in gasless_chains:
                        if self._stop_event.is_set():
                            break
                        tgt_cid: int = gc["chain_id"]
                        if tgt_cid not in relay_chain_ids:
                            logger.info(
                                "[Wallet %s] Cannot refuel chain %s: not supported by Relay",
                                address[:10], tgt_cid,
                            )
                            continue

                        # Считаем дефицит газа в USD — нативные токены у разных цепей имеют разную цену
                        gasless_price_usd = float(
                            native_token_by_id.get(tgt_cid, {}).get("priceUSD")
                            or relay_native_by_id.get(tgt_cid, {}).get("priceUSD")
                            or 2000
                        )
                        gas_deficit = max(0, gc["max_gas_needed_wei"] - gc.get("eth_balance", 0))
                        if gas_deficit <= 0:
                            logger.info(
                                "[Wallet %s] Chain %s no longer needs refuel",
                                address[:10], tgt_cid,
                            )
                            continue
                        tokens_usd = sum(float(t.get("value_usd") or 0) for t in gc.get("tokens", []))
                        gas_usd = gc["max_gas_needed_wei"] / 1e18 * gasless_price_usd
                        if tokens_usd < REFUEL_MIN_VALUE_RATIO * gas_usd:
                            logger.info(
                                "[Wallet %s] Refuel %s skipped: tokens $%.2f < %d × gas $%.2f — not worth it",
                                address[:10], gc.get("debank_key") or tgt_cid,
                                tokens_usd, REFUEL_MIN_VALUE_RATIO, gas_usd,
                            )
                            continue
                        desired_out_wei = gas_deficit * 2  # 2× запас на целевой сети
                        refuel_usd = desired_out_wei / 1e18 * gasless_price_usd

                        donor_found = False
                        for donor_cid in donor_candidates:
                            if donor_cid == tgt_cid:
                                continue  # сеть без газа не может быть донором сама себе
                            try:
                                w3_d = rpc_resolver.get_web3(donor_cid)
                                donor_bal = await loop.run_in_executor(
                                    None, w3_d.eth.get_balance, address
                                )
                            except Exception:
                                continue
                            # Считаем USD-баланс донора по цене его нативного токена
                            donor_price_usd = float(
                                native_token_by_id.get(donor_cid, {}).get("priceUSD")
                                or relay_native_by_id.get(donor_cid, {}).get("priceUSD")
                                or 1
                            )
                            donor_bal_usd = donor_bal / 1e18 * donor_price_usd
                            # Донор покрывает рефьюел + $0.01 на газ бридж-транзакции донора
                            if donor_bal_usd < refuel_usd + 0.01:
                                continue
                            # Конвертируем нужную сумму в нативные единицы донора
                            refuel_amount = int(refuel_usd / donor_price_usd * 1e18)
                            max_refuel_amount = int(max(0.0, donor_bal_usd - 0.01) / donor_price_usd * 1e18)
                            if max_refuel_amount <= 0:
                                continue
                            ok = await refuel_chain(
                                address=address,
                                private_key=private_key,
                                donor_chain_id=donor_cid,
                                tgt_chain_id=tgt_cid,
                                refuel_amount_wei=refuel_amount,
                                min_required_out_wei=desired_out_wei,
                                max_refuel_amount_wei=max_refuel_amount,
                                relay_client=relay_client,
                                relay_native_by_id=relay_native_by_id,
                                rpc_resolver=rpc_resolver,
                                stop_event=self._stop_event,
                                ops=wallet_ops,
                                donor_price_usd=donor_price_usd,
                                route_label=(
                                    f"{name_by_id.get(donor_cid, donor_cid)} → {name_by_id.get(tgt_cid, tgt_cid)}"
                                ),
                            )
                            if ok:
                                refueled.append(gc)
                                donor_found = True
                                break
                        if not donor_found:
                            logger.info(
                                "[Wallet %s] No suitable donor to refuel chain %s",
                                address[:10], tgt_cid,
                            )

                    if refueled and not self._stop_event.is_set():
                        retry_result = await retry_gasless_swaps(
                            gasless_chains=refueled,
                            address=address,
                            private_key=private_key,
                            lifi_client=lifi_client,
                            rpc_resolver=rpc_resolver,
                            settings=settings,
                            native_token_by_id=native_token_by_id,
                            relay_native_by_id=relay_native_by_id,
                            lifi_chain_ids=lifi_chain_ids,
                            stop_event=self._stop_event,
                        )
                        wallet_ops.extend(retry_result.get("ops", []))
                        swaps_attempted += int(retry_result.get("swaps_attempted", 0))
                        if retry_result and retry_result.get("chains_processed"):
                            existing = swap_result.get("chains_processed", "")
                            new_processed = retry_result["chains_processed"]
                            swap_result["chains_processed"] = (
                                existing + ", " + new_processed if existing else new_processed
                            )
                            swap_result["tokens_swapped"] = (
                                swap_result.get("tokens_swapped", 0) + retry_result.get("tokens_swapped", 0)
                            )
                            swap_result["total_collected_usd"] = round(
                                swap_result.get("total_collected_usd", 0.0) + retry_result.get("total_usd", 0.0), 2
                            )

                result_data.update(swap_result)
                # Удаляем поля избыточные для таблицы
                for _k in ("private_key", "address", "chains_processed", "chains_skipped"):
                    result_data.pop(_k, None)
                # Две разные суммы — две колонки: свопы в нативку и отправленное бриджем.
                # Раньше одна колонка значила то одно, то другое в зависимости от кошелька.
                result_data["swapped_usd"] = result_data.pop("total_collected_usd", 0.0)
                result_data["total_collected_usd"] = 0.0
                result_data["target"] = name_by_id.get(wallet_tgt_id, str(wallet_tgt_id))

                async def _exchange_step() -> tuple[str | None, bool]:
                    """Перевод на биржу из целевой сети. (tx, упал ли). Средства могут быть
                    уже там (бриджить нечего) — перевод всё равно выполняется; раньше он шёл
                    только после бриджа в этом же прогоне, и повторный запуск его не делал."""
                    price = float(
                        native_token_by_id.get(wallet_tgt_id, {}).get("priceUSD")
                        or relay_native_by_id.get(wallet_tgt_id, {}).get("priceUSD")
                        or 0
                    )
                    tx = await send_to_exchange(
                        address=address,
                        private_key=private_key,
                        exchange_address=subaccounts[wallet_idx],
                        tgt_chain_id=wallet_tgt_id,
                        rpc_resolver=rpc_resolver,
                        gas_prices=gas_prices,
                        settings=settings,
                        stop_event=self._stop_event,
                        ops=wallet_ops,
                        price_usd=price,
                    )
                    failed = any(op["type"] == "exchange" and op["status"] == "FAILED" for op in wallet_ops)
                    return tx, failed

                def _fill_ops_fields() -> None:
                    ok_swaps = int(result_data.get("tokens_swapped", 0) or 0)
                    result_data["swaps_summary"] = f"{ok_swaps}/{swaps_attempted}" if swaps_attempted else ""
                    result_data["refuel_usd"] = round(sum(
                        op["usd"] for op in wallet_ops
                        if op["type"] == "refuel" and op["status"] != "REVERTED"
                    ), 2)
                    result_data["_detail_ops"] = wallet_ops   # полный список — только для экспорта

                processed_keys = _build_processed_bridge_keys(
                    chains_processed=swap_result.get("chains_processed", ""),
                    target_chains=settings.target_chains,
                )
                if not processed_keys:
                    exchange_failed = False
                    if settings.send_to_exchange and not self._stop_event.is_set():
                        exchange_tx, exchange_failed = await _exchange_step()
                        result_data["exchange_tx"] = exchange_tx or ""
                    status, error = _wallet_status(
                        [], wallet_ops, int(result_data.get("tokens_swapped") or 0), exchange_tx, exchange_failed,
                    )
                    _fill_ops_fields()
                    return Result(
                        item=address,
                        status=status,
                        data=result_data,
                        error=error,
                    ), True

                # Бридж из каждой активной сети
                for src_key in processed_keys:
                    if self._stop_event.is_set():
                        break
                    src_chain_id = DEBANK_TO_CHAIN_ID.get(src_key)
                    if not src_chain_id:
                        continue

                    # ШАГ 3: бридж
                    b_tx, b_status, b_tgt_id, b_sent_usd = await bridge_native(
                        address=address,
                        private_key=private_key,
                        lifi_client=lifi_client,
                        relay_client=relay_client,
                        rpc_resolver=rpc_resolver,
                        settings=settings,
                        lifi_chain_ids=lifi_chain_ids,
                        relay_chain_ids=relay_chain_ids,
                        native_token_by_id=native_token_by_id,
                        relay_native_by_id=relay_native_by_id,
                        tgt_chain_id=wallet_tgt_id,
                        src_chain_id=src_chain_id,
                        gas_prices=gas_prices,
                        stop_event=self._stop_event,
                        ops=wallet_ops,
                    )
                    total_sent_usd += b_sent_usd

                    bridge_ops.append({
                        "src": src_key,
                        "tgt": name_by_id.get(b_tgt_id, str(b_tgt_id)) if b_tgt_id else "",
                        "tx": b_tx or "",
                        "status": b_status,
                        "usd": round(b_sent_usd, 4),
                    })
                    if b_tx or b_status in _BRIDGE_ATTEMPT_STATUSES:
                        wallet_ops.append({
                            "type": "bridge",
                            "chain": f"{src_key} → {name_by_id.get(wallet_tgt_id, wallet_tgt_id)}",
                            "detail": "",
                            "usd": round(b_sent_usd, 4),
                            "tx": b_tx or "",
                            "status": b_status,
                        })


                    # Задержка после каждого выполненного бриджа
                    if b_status in ("COMPLETED", "SENT") and not self._stop_event.is_set():
                        delay = random.randint(settings.delay_min, settings.delay_max)
                        logger.info(
                            "[Wallet %s] Delay %ds after bridge from %s (status=%s)",
                            address[:10], delay, src_key, b_status,
                        )
                        await _sleep_interruptible(delay)

                # ШАГ 4: отправка на биржу — один раз после всех бриджей. Не ждём
                # успешного бриджа: на целевой сети могут быть и прежние средства.
                # Пропускаем только если бридж ещё в пути — иначе ушла бы неполная сумма.
                exchange_failed = False
                in_flight = [op["src"] for op in bridge_ops if op["status"] in ("SENT", "TIMEOUT", "STOPPED")]
                if settings.send_to_exchange and not self._stop_event.is_set():
                    if in_flight:
                        logger.warning(
                            "[Wallet %s] Exchange transfer skipped: bridge still in flight from %s — "
                            "rerun later to send", address[:10], ", ".join(in_flight),
                        )
                    else:
                        exchange_tx, exchange_failed = await _exchange_step()

                # total_collected_usd = сумма фактически отправленных через бридж средств
                result_data["total_collected_usd"] = round(total_sent_usd, 2)

                # Bridges: успешные / попытки (реально отправленные tx). Сети с пылью
                # (BELOW_MIN) и без маршрута — не попытки: раньше выходило «4/31».
                attempts = [op for op in bridge_ops if op["tx"] or op["status"] in _BRIDGE_ATTEMPT_STATUSES]
                if attempts:
                    ok_count = sum(1 for op in attempts if op["status"] in ("COMPLETED", "SENT"))
                    result_data["bridge_summary"] = f"{ok_count}/{len(attempts)}"
                else:
                    result_data["bridge_summary"] = ""
                _fill_ops_fields()
                result_data["exchange_tx"] = exchange_tx or ""

                # Статус — по результатам, а не по «лучшему» статусу бриджа: раньше
                # NO_RPC сети с пылью давал ERROR при успешном переводе на биржу.
                result_status, result_error = _wallet_status(
                    bridge_ops, wallet_ops, int(result_data.get("tokens_swapped") or 0), exchange_tx, exchange_failed,
                )

                logger.log(
                    SUCCESS if result_status == ResultStatus.OK else logging.INFO,
                    "[Wallet %s] Result: %s (bridges %s%s)",
                    address, result_status.name, result_data.get("bridge_summary") or "0/0",
                    ", exchange sent" if exchange_tx else ", exchange FAILED" if exchange_failed else "",
                )
                return Result(
                    item=address,
                    status=result_status,
                    data=result_data,
                    error=result_error,
                ), True

            except Exception as e:
                logger.exception("[Wallet %d] Unexpected error: %s", wallet_idx, e)
                return Result(
                    item=wallet_label,
                    status=ResultStatus.ERROR,
                    error=str(e),
                ), True

        queue: asyncio.Queue = asyncio.Queue()
        for item in enumerate(wallets):
            queue.put_nowait(item)
        out: asyncio.Queue = asyncio.Queue()

        async def worker() -> None:
            try:
                while not self._stop_event.is_set():
                    try:
                        wallet_idx, wallet = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    result, delay_after = await process_wallet(wallet_idx, wallet)
                    await out.put(result)
                    # Задержка перед следующим кошельком этого воркера
                    if delay_after and not queue.empty() and not self._stop_event.is_set():
                        delay = random.randint(settings.delay_min, settings.delay_max)
                        logger.info(
                            "[Wallet %d] Delay %ds before next wallet...",
                            wallet_idx + 1, delay,
                        )
                        await _sleep_interruptible(delay)
            finally:
                await out.put(None)  # воркер завершился

        workers = [asyncio.create_task(worker()) for _ in range(parallel)]
        try:
            finished = 0
            while finished < len(workers):
                result = await out.get()
                if result is None:
                    finished += 1
                    continue
                self._results.append(result)
                yield result
        finally:
            for w in workers:
                w.cancel()
            logger.info("Collector finished. Total: %d results.", len(self._results))
            self._signals.run_complete.emit(list(self._results), {})

    async def stop(self) -> None:
        self._stop_event.set()
