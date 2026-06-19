from __future__ import annotations
import asyncio
import threading
from typing import AsyncIterator

from PySide6.QtCore import QObject, Signal

from app.core.base_module import BaseModule
from app.core.models import RunContext, Result, ResultStatus, ColumnDef
from app.integrations.debank_client import DeBankClient
from app.integrations.proxy_utils import ProxyRotator

RETRY_ATTEMPTS = 10
MIN_VALUE_DISPLAY = 0.01
ZERO_ADDR = "0x0000000000000000000000000000000000000000"

# ── Anti-phantom corroboration ──────────────────────────────────────────────
# Под высокой параллельной нагрузкой DeBank API ~1 раз из 30 возвращает портфель
# ЧУЖОГО адреса. Ответ внутренне согласован (реальные токены/пулы), поэтому
# одиночная выборка или пересчёт по одному снимку фантом не ловит — кошелёк на
# ~$15 показывает тысячи/миллионы $. Особенность: истинный баланс СТАБИЛЕН между
# запросами, фантом — СЛУЧАЙНЫЙ и не повторяется.
#
# Решение — подтверждение (corroboration): принимаем баланс только когда >=2
# независимые выборки (разные прокси) сходятся по total_usd. Случайный фантом
# почти никогда не повторится → отбрасывается; стабильное реальное значение
# подтверждается. Типовая цена — ~2 запроса на кошелёк.
CORROBORATION_MIN_AGREE = 2        # сколько согласных выборок нужно для приёма
CORROBORATION_TOL_PCT = 0.02       # относительный допуск (2%)
CORROBORATION_TOL_ABS = 1.0        # абсолютный допуск ($1) — для мелких балансов
CORROBORATION_MAX_FETCHES = 5      # бюджет УСПЕШНЫХ выборок на кошелёк


def _asset_value_usd(token: dict) -> float:
    return (token.get("price", 0) or 0) * (token.get("amount", 0) or 0)


def _is_native_asset(token: dict) -> bool:
    """DeBank marks ERC-20 balances with a hex token id; native assets use the chain key."""
    token_id = str(token.get("id", "") or "").lower()
    contract = str(token.get("contract_address", "") or "").lower()
    if contract and contract != ZERO_ADDR:
        return False
    return not token_id.startswith("0x")


def _fetch_snapshot(address: str, proxy_url: str) -> dict:
    """Одна независимая выборка кошелька через DeBank (один прокси/сессия).

    Возвращает «снимок» {total_usd, tokens}. Токены и total_usd берутся в рамках
    одного клиента, поэтому относятся к одному и тому же ответу API.
    """
    client = DeBankClient(proxy=proxy_url)
    tokens = client.get_tokens(address)
    total_usd = client.get_total_usd(address)
    try:
        total_usd = float(total_usd or 0.0)
    except (TypeError, ValueError):
        total_usd = 0.0
    return {"total_usd": total_usd, "tokens": tokens if isinstance(tokens, list) else []}


def _values_agree(a: float, b: float) -> bool:
    """Согласованы ли два знач.total_usd в пределах абсолютного ИЛИ относительного допуска."""
    diff = abs(a - b)
    if diff <= CORROBORATION_TOL_ABS:
        return True
    return diff <= CORROBORATION_TOL_PCT * max(abs(a), abs(b), 1.0)


def _agreeing_cluster(snapshots: list[dict]) -> list[dict] | None:
    """Наибольший кластер согласных по total_usd выборок (размером >= MIN_AGREE).

    Возвращает None, пока ни один кластер не набрал нужного числа подтверждений.
    """
    best: list[dict] | None = None
    for anchor in snapshots:
        cluster = [s for s in snapshots if _values_agree(s["total_usd"], anchor["total_usd"])]
        if len(cluster) >= CORROBORATION_MIN_AGREE and (best is None or len(cluster) > len(best)):
            best = cluster
    return best


def _representative(cluster: list[dict]) -> dict:
    """Из согласного кластера берём выборку с медианным total_usd (стабильный выбор)."""
    ordered = sorted(cluster, key=lambda s: s["total_usd"])
    return ordered[len(ordered) // 2]


def _conservative_pick(snapshots: list[dict]) -> dict:
    """Бюджет исчерпан без согласия → консервативный выбор.

    Берём выборку с наименьшим total_usd: это исключает раздувание фантомом
    (фантом почти всегда крупнее реального мелкого баланса).
    """
    return min(snapshots, key=lambda s: s["total_usd"])


def _build_result(address: str, snapshot: dict, corroborated: bool) -> Result:
    """Строит Result.OK из выбранного (подтверждённого) снимка."""
    tokens = snapshot["tokens"]
    total_usd = snapshot["total_usd"]

    tokens_data = [
        {
            "symbol": t.get("symbol", "?"),
            "chain":  t.get("chain", "?"),
            "amount": t.get("amount", 0),
            "price":  t.get("price", 0),
            "value":  round(_asset_value_usd(t), 2),
        }
        for t in tokens
        if round(_asset_value_usd(t), 2) >= MIN_VALUE_DISPLAY
    ]
    tokens_data.sort(key=lambda x: x["value"], reverse=True)

    chains = sorted({t["chain"] for t in tokens_data})

    # Native asset total (ETH, BNB, MATIC, etc.)
    native_usd = round(sum(
        _asset_value_usd(t) for t in tokens
        if _is_native_asset(t)
    ), 2)

    top_tokens = ", ".join(
        f"{t['symbol']}(${t['value']:.2f})" for t in tokens_data[:3]
    )

    # Value per chain for top_chain_usd
    chain_totals: dict[str, float] = {}
    for t in tokens_data:
        chain_totals[t["chain"]] = chain_totals.get(t["chain"], 0) + t["value"]
    if chain_totals:
        best_chain = max(chain_totals, key=chain_totals.get)  # type: ignore[arg-type]
        top_chain_usd = f"{best_chain}: ${chain_totals[best_chain]:.0f}"
    else:
        top_chain_usd = ""

    return Result(
        item=address,
        status=ResultStatus.OK,
        data={
            "total_usd":      round(total_usd, 2),
            "native_usd":     native_usd,
            "top_tokens":     top_tokens,
            "active_chains":  ", ".join(chains),
            "top_chain_usd":  top_chain_usd,
            "verified":       "✓" if corroborated else "⚠",
            "_detail":        {"tokens_data": tokens_data},
        },
    )


def _check_wallet_sync(
    address: str,
    rotator: ProxyRotator,
    stop_event: threading.Event,
) -> Result:
    """Sync функция для run_in_executor. Проверяет один кошелёк с подтверждением.

    Делает независимые выборки (разные прокси), пока >=CORROBORATION_MIN_AGREE из
    них не сойдутся по total_usd — это отсекает фантомные балансы DeBank,
    возникающие при высокой параллельности. Сетевые ошибки не тратят бюджет
    подтверждения (есть запас попыток).
    """
    last_error: Exception | None = None
    snapshots: list[dict] = []
    attempts = 0
    max_attempts = CORROBORATION_MAX_FETCHES + RETRY_ATTEMPTS  # запас на сетевые сбои

    while attempts < max_attempts and len(snapshots) < CORROBORATION_MAX_FETCHES:
        if stop_event.is_set():
            return Result(item=address, status=ResultStatus.ERROR, error="Stopped")

        proxy = rotator.next()
        if proxy is None:
            return Result(item=address, status=ResultStatus.ERROR,
                          error="Нет доступных прокси")

        attempts += 1
        try:
            snap = _fetch_snapshot(address, proxy.to_url())
        except Exception as e:
            last_error = e
            continue

        snapshots.append(snap)
        cluster = _agreeing_cluster(snapshots)
        if cluster is not None:
            return _build_result(address, _representative(cluster), corroborated=True)

    # Бюджет исчерпан без согласия — консервативный выбор, помечаем непроверенным.
    if snapshots:
        return _build_result(address, _conservative_pick(snapshots), corroborated=False)

    return Result(
        item=address,
        status=ResultStatus.ERROR,
        error=str(last_error) if last_error else "Unknown error",
    )


class _EvmSignals(QObject):
    # _EvmSignals создаётся в __init__ (main thread) — никогда не создавать в run()!
    # Это обеспечивает Qt thread affinity для корректной queued-доставки из worker thread.
    run_complete = Signal(list, dict)


class EvmBalanceCheckerModule(BaseModule):
    name = "EVM Balance"

    def column_schema(self) -> list[ColumnDef]:
        return [
            ColumnDef(key="item",           label="Address",      width=200),
            ColumnDef(key="status",         label="Status"),
            ColumnDef(key="total_usd",      label="Total $",      fmt="${:.2f}", sort_type="numeric"),
            ColumnDef(key="native_usd",     label="Native $",     fmt="${:.2f}", sort_type="numeric"),
            ColumnDef(key="verified",       label="✓",            width=40),
            ColumnDef(key="top_tokens",     label="Top Tokens"),
            ColumnDef(key="active_chains",  label="Chains"),
            ColumnDef(key="top_chain_usd",  label="Top Chain"),
        ]

    def __init__(self) -> None:
        from app.ui.module_views.evm_balance_view import EvmBalanceConfigWidget
        self._signals = _EvmSignals()
        self._results: list[Result] = []
        self._details: dict[str, dict] = {}
        self._widget = EvmBalanceConfigWidget()
        self._signals.run_complete.connect(self._widget.on_run_complete)
        self._stop_event = threading.Event()

    def get_config_widget(self):
        return self._widget

    def get_item_count(self) -> int:
        """Для прогресс-бара MainWindow."""
        return len(self._widget.get_wallets())

    def get_results(self) -> list[Result]:
        return list(self._results)

    async def run(self, ctx: RunContext) -> AsyncIterator[Result]:
        self._results.clear()
        self._details.clear()
        self._stop_event.clear()

        wallets = self._widget.get_wallets()
        proxies = self._widget.get_proxies()
        rotator = ProxyRotator(proxies)
        semaphore = asyncio.Semaphore(ctx.concurrency)
        loop = asyncio.get_running_loop()

        async def _indexed_check(idx: int, addr: str) -> tuple[int, Result]:
            async with semaphore:
                result = await loop.run_in_executor(
                    None, _check_wallet_sync, addr, rotator, self._stop_event
                )
                return idx, result

        tasks = [asyncio.create_task(_indexed_check(i, addr)) for i, addr in enumerate(wallets)]
        buffer: dict[int, Result] = {}
        next_idx = 0
        try:
            for fut in asyncio.as_completed(tasks):
                if self._stop_event.is_set():
                    for t in tasks:
                        t.cancel()
                    break
                idx, result = await fut
                buffer[idx] = result
                while next_idx in buffer:
                    r = buffer.pop(next_idx)
                    detail = r.data.pop("_detail", {})
                    self._results.append(r)
                    self._details[r.item] = detail
                    yield r
                    next_idx += 1
        finally:
            self._signals.run_complete.emit(list(self._results), dict(self._details))

    async def stop(self) -> None:
        self._stop_event.set()
