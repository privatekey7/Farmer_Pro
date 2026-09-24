from __future__ import annotations
import csv
import json
from abc import ABC, abstractmethod
from pathlib import Path
import openpyxl
from app.core.models import Result, ResultStatus


def get_columns(results: list[Result]) -> list[str]:
    """Колонки — объединение ключей result.data по всем строкам (в порядке
    появления). Служебные поля "_…" (детали для экспорта модуля) не выгружаются.
    Fallback: item, status."""
    columns = ["item", "status"]
    for r in results:
        for k in r.data.keys():
            if k not in columns and not k.startswith("_"):
                columns.append(k)
    return columns


def _result_to_row(result: Result, columns: list[str]) -> dict:
    row = {
        "item": result.item,
        "status": result.status.value,
    }
    row.update(result.data)
    if result.status == ResultStatus.UNVERIFIED:
        # Значение не подтверждено — суммы в экспорт не пишутся (как в DeBankChecker).
        row = {k: ("" if k.endswith("_usd") else v) for k, v in row.items()}
    return {col: row.get(col, "") for col in columns}


def _total_row(results: list[Result], columns: list[str]) -> dict | None:
    """Итоговая строка для модулей с балансами (есть total_usd): суммы всех
    *_usd колонок только по OK-кошелькам — UNVERIFIED/ERROR не подтверждены
    и в итог не входят (как итог под таблицей)."""
    if "total_usd" not in columns:
        return None
    ok = [r for r in results if r.status == ResultStatus.OK]
    row = {col: "" for col in columns}
    row["item"] = "TOTAL"
    row["status"] = f"{len(ok)}/{len(results)} ok"
    for col in columns:
        if col.endswith("_usd"):
            values = [r.data.get(col) for r in ok]
            row[col] = round(sum(v for v in values if isinstance(v, (int, float))), 2)
    return row


_OPERATION_COLUMNS = ["address", "type", "chain", "detail", "usd", "tx_hash", "status"]


def _operation_rows(results: list[Result]) -> list[dict]:
    """Все отправленные транзакции (коллектор кладёт их в "_detail_ops").

    Раньше они были только в экспорте из панели коллектора — кнопки под
    таблицей давали одну сводку без транзакций.
    """
    rows = []
    for r in results:
        for op in r.data.get("_detail_ops") or []:
            rows.append({
                "address": r.item,
                "type": op.get("type", ""),
                "chain": op.get("chain", ""),
                "detail": op.get("detail", ""),
                "usd": op.get("usd", 0),
                "tx_hash": op.get("tx", ""),
                "status": op.get("status", ""),
            })
    return rows


class BaseExporter(ABC):
    @abstractmethod
    def export(self, results: list[Result], path: str) -> None: ...


class CsvExporter(BaseExporter):
    def export(self, results: list[Result], path: str) -> None:
        columns = get_columns(results)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for r in results:
                writer.writerow(_result_to_row(r, columns))
            total = _total_row(results, columns)
            if total:
                writer.writerow(total)
            ops = _operation_rows(results)
            if ops:
                plain = csv.writer(f)
                plain.writerow([])
                plain.writerow(["=== OPERATIONS ==="])
                plain.writerow(_OPERATION_COLUMNS)
                for op in ops:
                    plain.writerow([op[c] for c in _OPERATION_COLUMNS])


class JsonExporter(BaseExporter):
    def export(self, results: list[Result], path: str) -> None:
        columns = get_columns(results)
        rows = [_result_to_row(r, columns) for r in results]
        ops = _operation_rows(results)
        payload = {"summary": rows, "operations": ops} if ops else rows
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


class XlsxExporter(BaseExporter):
    def export(self, results: list[Result], path: str) -> None:
        columns = get_columns(results)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(columns)
        for r in results:
            row = _result_to_row(r, columns)
            ws.append([row.get(col, "") for col in columns])
        total = _total_row(results, columns)
        if total:
            ws.append([total[col] for col in columns])
        ops = _operation_rows(results)
        if ops:
            ws.title = "Summary"
            ws_ops = wb.create_sheet("Operations")
            ws_ops.append(_OPERATION_COLUMNS)
            for op in ops:
                ws_ops.append([op[c] for c in _OPERATION_COLUMNS])
        wb.save(path)
