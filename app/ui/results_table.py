from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QLineEdit, QPushButton, QHeaderView, QLabel, QComboBox, QStackedWidget,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from app.core.models import Result, ResultStatus
from app.storage.exporters import get_columns, CsvExporter, JsonExporter, XlsxExporter
from app.ui.widgets.empty_state import EmptyState
from app.i18n import tr, i18n

# Status colors
_STATUS_COLORS = {
    "ok":    QColor("#30D158"),
    "error": QColor("#FF453A"),
    "skip":  QColor("#F0C040"),
}

_STATUS_BG = {
    "ok":    QColor(48, 209, 88, 25),
    "error": QColor(255, 69, 58, 25),
    "skip":  QColor(240, 192, 64, 25),
}

_ALT_ROW_COLOR = QColor(255, 255, 255, 6)


class ResultsTable(QWidget):
    """Таблица результатов с поиском, фильтром, статус-бейджами и empty state."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._results: list[Result] = []
        self._columns: list[str] = ["item", "status"]

        # Search bar
        self._search = QLineEdit()
        self._search.setObjectName("resultsSearch")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)

        # Table
        self._table = QTableWidget()
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setShowGrid(False)

        # Empty state
        self._empty = EmptyState(
            icon_text="📋",
            title=tr("empty_results_title"),
            subtitle=tr("empty_results_subtitle"),
        )

        # Stacked: empty ↔ table
        self._stack = QStackedWidget()
        self._stack.addWidget(self._empty)     # index 0
        self._stack.addWidget(self._table)      # index 1
        self._stack.setCurrentIndex(0)

        # Quality filter
        self._quality_filter = QComboBox()
        self._quality_filter.addItem(tr("filter_all"), "all")
        for q in ("high", "medium", "low"):
            self._quality_filter.addItem(q, q)
        self._quality_filter.setVisible(False)

        # Export buttons
        self._export_csv = QPushButton()
        self._export_json = QPushButton()
        self._export_xlsx = QPushButton()
        self._export_csv.clicked.connect(self._on_export_csv)
        self._export_json.clicked.connect(self._on_export_json)
        self._export_xlsx.clicked.connect(self._on_export_xlsx)
        self._export_csv.setVisible(False)
        self._export_json.setVisible(False)
        self._export_xlsx.setVisible(False)

        # Footer stats
        self._total_label = QLabel("")
        self._total_label.setVisible(False)
        self._stats_label = QLabel("")
        self._stats_label.setObjectName("statsLabel")
        self._stats_label.setVisible(False)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._quality_filter)
        btn_row.addWidget(self._export_csv)
        btn_row.addWidget(self._export_json)
        btn_row.addWidget(self._export_xlsx)
        btn_row.addStretch()
        btn_row.addWidget(self._stats_label)
        btn_row.addWidget(self._total_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._search)
        layout.addWidget(self._stack)
        layout.addLayout(btn_row)

        self.retranslate_ui()
        i18n.language_changed.connect(self.retranslate_ui)

    def retranslate_ui(self) -> None:
        self._search.setPlaceholderText(tr("search_placeholder"))
        self._export_csv.setText(tr("export_csv_btn"))
        self._export_json.setText(tr("export_json_btn"))
        self._export_xlsx.setText(tr("export_xlsx_btn"))
        self._quality_filter.setItemText(0, tr("filter_all"))
        self._empty.set_texts(
            title=tr("empty_results_title"),
            subtitle=tr("empty_results_subtitle"),
        )

    def add_row(self, result: Result) -> None:
        self._results.append(result)
        self._stack.setCurrentIndex(1)  # show table

        if not self._table.columnCount() or (result.data and self._columns == ["item", "status"]):
            self._columns = get_columns(self._results)
            self._table.setColumnCount(len(self._columns))
            self._table.setHorizontalHeaderLabels(self._columns)

        row = self._table.rowCount()
        self._table.insertRow(row)
        row_data = {"item": result.item, "status": result.status.value}
        row_data.update(result.data)

        for col_idx, col in enumerate(self._columns):
            cell_text = str(row_data.get(col, ""))
            item = QTableWidgetItem(cell_text)
            item.setTextAlignment(Qt.AlignCenter)

            # Color status column
            if col == "status":
                status_val = result.status.value
                fg = _STATUS_COLORS.get(status_val)
                bg = _STATUS_BG.get(status_val)
                if fg:
                    item.setForeground(fg)
                if bg:
                    item.setBackground(bg)

            self._table.setItem(row, col_idx, item)

        if any(r.data.get("quality") for r in self._results):
            self._quality_filter.setVisible(True)
        self._export_csv.setVisible(True)
        self._export_json.setVisible(True)
        self._export_xlsx.setVisible(True)
        self._update_total()
        self._update_stats()

    def _update_total(self) -> None:
        total = sum(r.data.get("total_usd", 0) for r in self._results if r.data)
        if total > 0:
            self._total_label.setText(tr("total_fmt").format(total=total))
            self._total_label.setVisible(True)
        else:
            self._total_label.setVisible(False)

    def _update_stats(self) -> None:
        ok = sum(1 for r in self._results if r.status == ResultStatus.OK)
        err = sum(1 for r in self._results if r.status == ResultStatus.ERROR)
        skip = sum(1 for r in self._results if r.status == ResultStatus.SKIP)
        parts = [f"✓ {ok}"]
        if err:
            parts.append(f"✗ {err}")
        if skip:
            parts.append(f"⊘ {skip}")
        self._stats_label.setText("  │  ".join(parts))
        self._stats_label.setVisible(True)

    def snapshot(self) -> list[Result]:
        return list(self._results)

    def restore(self, results: list[Result]) -> None:
        self.clear_results()
        for r in results:
            self.add_row(r)

    def clear_results(self) -> None:
        self._results.clear()
        self._table.setRowCount(0)
        self._table.setColumnCount(0)
        self._columns = ["item", "status"]
        self._quality_filter.setCurrentIndex(0)
        self._quality_filter.setVisible(False)
        self._export_csv.setVisible(False)
        self._export_json.setVisible(False)
        self._export_xlsx.setVisible(False)
        self._total_label.setVisible(False)
        self._stats_label.setVisible(False)
        self._stack.setCurrentIndex(0)  # show empty state

    def _apply_filter(self, text: str) -> None:
        text = text.lower()
        for row in range(self._table.rowCount()):
            match = any(
                text in (self._table.item(row, col).text().lower() if self._table.item(row, col) else "")
                for col in range(self._table.columnCount())
            )
            self._table.setRowHidden(row, not match)

    def _filtered_results(self) -> list[Result]:
        quality = self._quality_filter.currentData()
        if quality == "all":
            return self._results
        return [r for r in self._results if r.data.get("quality") == quality]

    def _on_export_csv(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, tr("save_csv"), "results.csv", "CSV (*.csv)")
        if path:
            CsvExporter().export(self._filtered_results(), path)

    def _on_export_json(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, tr("save_json"), "results.json", "JSON (*.json)")
        if path:
            JsonExporter().export(self._filtered_results(), path)

    def _on_export_xlsx(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, tr("save_xlsx"), "results.xlsx", "Excel (*.xlsx)")
        if path:
            XlsxExporter().export(self._filtered_results(), path)
