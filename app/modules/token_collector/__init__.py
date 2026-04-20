# app/modules/token_collector/__init__.py
from __future__ import annotations
import asyncio
import logging
import random
import threading
from typing import AsyncIterator

from PySide6.QtCore import QObject, Signal

from app.core.base_module import BaseModule
from app.core.models import RunContext, Result, ResultStatus
from app.integrations.debank_client import DeBankClient
from app.integrations.lifi_client import (
    LiFiClient, DEBANK_TO_CHAIN_ID, LiFiChainRegistry,
)
from app.integrations.relay_client import RelayClient
from app.integrations.rpc_resolver import RpcResolver
from app.integrations.proxy_utils import ProxyRotator
from app.integrations.chainlist_client import fetch_chainlist_rpcs
from app.modules.token_collector._collector_logic import fetch_and_swap, retry_gasless_swaps
from app.modules.token_collector._bridge_logic import bridge_native, send_to_exchange, refuel_chain

logger = logging.getLogger(__name__)


class _CollectorSignals(QObject):
    # Создаётся в __init__ (main thread) — обеспечивает Qt thread affinity
    run_complete = Signal(list, dict)


class CollectorModule(BaseModule):
    name = "Collector"

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

        proxy = rotator.next()
        proxy_url = proxy.to_url() if proxy else None
        lifi_client = LiFiClient(proxy=proxy_url)
        relay_client = RelayClient(proxy=proxy_url)

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

        rpc_resolver = RpcResolver(lifi_rpc_by_id, relay_rpc_by_id, chainlist_rpcs)

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

        # ── ШАГ 1–4: обработка кошельков последовательно ─────────────────
        logger.info("Starting Collector: %d wallets, target chains: %s", len(wallets), settings.target_chains)
        try:
            for wallet_idx, wallet in enumerate(wallets):
                if self._stop_event.is_set():
                    break

                logger.info(
                    "── Wallet %d/%d ──────────────────────────────",
                    wallet_idx + 1, len(wallets),
                )

                proxy = rotator.next()
                proxy_url = proxy.to_url() if proxy else None
                debank_client = DeBankClient(proxy=proxy_url or "http://127.0.0.1:8080")

                result_data: dict = {}
                bridge_tx: str | None = None
                bridge_ops: list[dict] = []
                bridge_status = ""
                exchange_tx: str | None = None
                total_sent_usd = 0.0

                try:
                    # ШАГ 1-2: DeBank + swap
                    swap_result = await fetch_and_swap(
                        wallet=wallet,
                        lifi_client=lifi_client,
                        debank_client=debank_client,
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
                        result = Result(
                            item=wallet["raw"][:42],
                            status=ResultStatus.ERROR,
                            error="fetch_and_swap failed",
                        )
                        self._results.append(result)
                        yield result
                        continue

                    address = swap_result["address"]
                    private_key = swap_result["private_key"]

                    # ── ШАГ 2.5–2.6: рефьюел и повторный своп на gasless цепях ──
                    gasless_chains = swap_result.pop("gasless_chains", [])
                    if gasless_chains and not self._stop_event.is_set():
                        target_key_set_local = set(settings.target_chains)
                        # Кандидаты-доноры: обработанные не-target цепи + сами target-цепи
                        donor_candidates: list[int] = []
                        for k in swap_result.get("chains_processed", "").split(", "):
                            if not k or k in target_key_set_local:
                                continue
                            cid = DEBANK_TO_CHAIN_ID.get(k)
                            if cid and cid in relay_chain_ids:
                                donor_candidates.append(cid)
                        # Target-цепи часто имеют больше всего нативки (например Soneium с ETH)
                        for tc_key in target_key_set_local:
                            tc_cid = DEBANK_TO_CHAIN_ID.get(tc_key.lower())
                            if tc_cid and tc_cid in relay_chain_ids and tc_cid not in donor_candidates:
                                donor_candidates.append(tc_cid)

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
                            gas_deficit = gc["max_gas_needed_wei"] - gc.get("eth_balance", 0)
                            refuel_usd = gas_deficit / 1e18 * gasless_price_usd * 2  # 2× запас

                            donor_found = False
                            for donor_cid in donor_candidates:
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
                                ok = await refuel_chain(
                                    address=address,
                                    private_key=private_key,
                                    donor_chain_id=donor_cid,
                                    tgt_chain_id=tgt_cid,
                                    refuel_amount_wei=refuel_amount,
                                    relay_client=relay_client,
                                    relay_native_by_id=relay_native_by_id,
                                    rpc_resolver=rpc_resolver,
                                    stop_event=self._stop_event,
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

                    # Выбираем src_chain — сеть с наибольшим балансом
                    # Простая эвристика: первая из processed
                    # Target chains участвуют только в свапе, но не в бридже FROM
                    target_key_set = set(settings.target_chains)
                    processed_keys = [
                        k for k in swap_result.get("chains_processed", "").split(", ")
                        if k and k not in target_key_set
                    ]
                    if not processed_keys:
                        result = Result(
                            item=address,
                            status=ResultStatus.SKIP,
                            data=result_data,
                        )
                        self._results.append(result)
                        yield result
                        # Посекундная задержка перед следующим кошельком
                        if wallet_idx < len(wallets) - 1:
                            delay = random.randint(settings.delay_min, settings.delay_max)
                            for _ in range(delay):
                                if self._stop_event.is_set():
                                    break
                                await asyncio.sleep(1)
                        continue

                    # Бридж из каждой активной сети
                    # Приоритет статусов: чем меньше — тем лучше
                    _BRIDGE_PRIORITY = {
                        "COMPLETED": 0, "SENT": 1, "PARTIAL": 2, "REFUNDED": 3,
                        "NODE_REJECTED": 4, "TX_REVERTED": 5, "NO_RPC": 6, "TIMEOUT": 7,
                        "NO_QUOTE": 8, "INSUFFICIENT": 9, "BELOW_MIN": 10, "NO_ROUTE": 11, "STOPPED": 12,
                    }
                    tgt_id = None
                    best_src_key = "?"
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
                            target_chain_ids=target_chain_ids,
                            src_chain_id=src_chain_id,
                            gas_prices=gas_prices,
                            stop_event=self._stop_event,
                        )
                        total_sent_usd += b_sent_usd

                        bridge_ops.append({
                            "src": src_key,
                            "tgt": name_by_id.get(b_tgt_id, str(b_tgt_id)) if b_tgt_id else "",
                            "tx": b_tx or "",
                            "status": b_status,
                            "usd": round(b_sent_usd, 4),
                        })

                        # Сохраняем лучший статус — не даём плохому перезаписать хороший
                        if _BRIDGE_PRIORITY.get(b_status, 99) < _BRIDGE_PRIORITY.get(bridge_status, 99):
                            bridge_tx = b_tx
                            bridge_status = b_status
                            tgt_id = b_tgt_id
                            best_src_key = src_key

                        # Задержка после каждого выполненного бриджа
                        if b_status in ("COMPLETED", "SENT") and not self._stop_event.is_set():
                            delay = random.randint(settings.delay_min, settings.delay_max)
                            logger.info(
                                "[Wallet %s] Delay %ds after bridge from %s (status=%s)",
                                address[:10], delay, src_key, b_status,
                            )
                            for _ in range(delay):
                                if self._stop_event.is_set():
                                    break
                                await asyncio.sleep(1)

                    # ШАГ 4: отправка на биржу — один раз после всех бриджей
                    if (
                        settings.send_to_exchange
                        and bridge_status in ("COMPLETED", "PARTIAL", "REFUNDED")
                        and tgt_id is not None
                        and not self._stop_event.is_set()
                    ):
                        exchange_tx = await send_to_exchange(
                            address=address,
                            private_key=private_key,
                            exchange_address=subaccounts[wallet_idx],
                            tgt_chain_id=tgt_id,
                            rpc_resolver=rpc_resolver,
                            gas_prices=gas_prices,
                            settings=settings,
                            stop_event=self._stop_event,
                        )

                    # total_collected_usd = сумма фактически отправленных через бридж средств
                    result_data["total_collected_usd"] = round(total_sent_usd, 2)

                    if tgt_id is not None:
                        last_tgt = name_by_id.get(tgt_id, str(tgt_id))
                        result_data["bridge_chain"] = f"{best_src_key} → {last_tgt}"
                    else:
                        result_data["bridge_chain"] = ""
                    result_data["bridge_tx"] = bridge_tx or ""
                    result_data["bridge_ops"] = bridge_ops
                    result_data["bridge_status"] = bridge_status
                    result_data["exchange_tx"] = exchange_tx or ""
                    _SKIP_STATUSES = ("NO_QUOTE", "NO_ROUTE", "BELOW_MIN", "STOPPED", "INSUFFICIENT", "NOT_INDEXED", "TX_REVERTED")

                    if bridge_status in ("COMPLETED", "PARTIAL", "SENT"):
                        result_status = ResultStatus.OK
                    elif bridge_status in _SKIP_STATUSES:
                        result_status = ResultStatus.SKIP
                    else:
                        result_status = ResultStatus.ERROR

                    logger.info(
                        "[Wallet %s] Result: %s (bridge_status=%s)",
                        address, result_status.name, bridge_status,
                    )
                    result = Result(
                        item=address,
                        status=result_status,
                        data=result_data,
                    )

                except Exception as e:
                    logger.exception("[Wallet %d] Unexpected error: %s", wallet_idx, e)
                    result = Result(
                        item=wallet.get("raw", "?")[:42],
                        status=ResultStatus.ERROR,
                        error=str(e),
                    )

                self._results.append(result)
                yield result

                # Задержка между кошельками
                if wallet_idx < len(wallets) - 1:
                    delay = random.randint(settings.delay_min, settings.delay_max)
                    logger.info(
                        "[Wallet %d] Delay %ds before next wallet...",
                        wallet_idx + 1, delay,
                    )
                    for _ in range(delay):
                        if self._stop_event.is_set():
                            break
                        await asyncio.sleep(1)

        finally:
            logger.info("Collector finished. Total: %d results.", len(self._results))
            self._signals.run_complete.emit(list(self._results), {})

    async def stop(self) -> None:
        self._stop_event.set()
