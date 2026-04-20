from __future__ import annotations
import csv
import json
from abc import ABC, abstractmethod
from pathlib import Path
import openpyxl
from app.core.models import Result


def get_columns(results: list[Result]) -> list[str]:
    """Определяет колонки из первого непустого result.data. Fallback: item, status."""
    for r in results:
        if r.data:
            base = ["item", "status"]
            extra = [k for k in r.data.keys() if k not in base]
            return base + extra
    return ["item", "status"]


def _result_to_row(result: Result, columns: list[str]) -> dict:
    row = {
        "item": result.item,
        "status": result.status.value,
    }
    row.update(result.data)
    return {col: row.get(col, "") for col in columns}


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


class JsonExporter(BaseExporter):
    def export(self, results: list[Result], path: str) -> None:
        columns = get_columns(results)
        rows = [_result_to_row(r, columns) for r in results]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)


class XlsxExporter(BaseExporter):
    def export(self, results: list[Result], path: str) -> None:
        columns = get_columns(results)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(columns)
        for r in results:
            row = _result_to_row(r, columns)
            ws.append([row.get(col, "") for col in columns])
        wb.save(path)
