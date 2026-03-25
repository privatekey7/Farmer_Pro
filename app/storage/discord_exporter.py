from __future__ import annotations
import csv
import json
from dataclasses import dataclass

import openpyxl

from app.core.models import Result


@dataclass
class DiscordExportConfig:
    status_filter: str | None = None  # None = all; "ok"/"invalid"/"disabled"


class DiscordExporter:
    _COLUMNS = ["token", "username", "email", "has_phone", "account_status"]

    def build(
        self,
        results: list[Result],
        config: DiscordExportConfig,
    ) -> list[dict]:
        rows = []
        for r in results:
            account_status = r.data.get("account_status", "")
            if config.status_filter and account_status != config.status_filter:
                continue
            rows.append({
                "token":          r.item,
                "username":       r.data.get("username") or "",
                "email":          r.data.get("email") or "",
                "has_phone":      "yes" if r.data.get("has_phone") else "no",
                "account_status": account_status,
            })
        return rows

    def export(
        self,
        results: list[Result],
        config: DiscordExportConfig,
        path: str,
        fmt: str,
    ) -> None:
        rows = self.build(results, config)
        if fmt == "csv":
            self._export_csv(rows, path)
        elif fmt == "json":
            self._export_json(rows, path)
        else:
            self._export_xlsx(rows, path)

    def _export_csv(self, rows: list[dict], path: str) -> None:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self._COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    def _export_json(self, rows: list[dict], path: str) -> None:
        json_rows = [{**row, "has_phone": row["has_phone"] == "yes"} for row in rows]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(json_rows, f, ensure_ascii=False, indent=2)

    def _export_xlsx(self, rows: list[dict], path: str) -> None:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Results"
        ws.append(self._COLUMNS)
        for row in rows:
            ws.append([row.get(c, "") for c in self._COLUMNS])
        wb.save(path)
