from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QLineEdit, QPushButton, QHeaderView, QLabel, QComboBox,
)
from PySide6.QtCore import Qt
from app.core.models import Result, ResultStatus
from app.storage.exporters import get_columns, CsvExporter, JsonExporter, XlsxExporter
from app.i18n import tr, i18n


class ResultsTable(QWidget):
    """Таблица результатов с поиском, фильтром качества и кнопками экспорта."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._results: list[Result] = []
        self._columns: list[str] = ["item", "status"]

        self._search = QLineEdit()
        self._search.textChanged.connect(self._apply_filter)

        self._table = QTableWidget()
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        self._quality_filter = QComboBox()
        self._quality_filter.addItem(tr("filter_all"), "all")
        for q in ("high", "medium", "low"):
            self._quality_filter.addItem(q, q)
        self._quality_filter.setVisible(False)

        self._export_csv = QPushButton()
        self._export_json = QPushButton()
        self._export_xlsx = QPushButton()
        self._export_csv.clicked.connect(self._on_export_csv)
        self._export_json.clicked.connect(self._on_export_json)
        self._export_xlsx.clicked.connect(self._on_export_xlsx)
        self._export_csv.setVisible(False)
        self._export_json.setVisible(False)
        self._export_xlsx.setVisible(False)

        self._total_label = QLabel("")
        self._total_label.setVisible(False)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._quality_filter)
        btn_row.addWidget(self._export_csv)
        btn_row.addWidget(self._export_json)
        btn_row.addWidget(self._export_xlsx)
        btn_row.addStretch()
        btn_row.addWidget(self._total_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._search)
        layout.addWidget(self._table)
        layout.addLayout(btn_row)

        self.retranslate_ui()
        i18n.language_changed.connect(self.retranslate_ui)

    def retranslate_ui(self) -> None:
        self._search.setPlaceholderText(tr("search_placeholder"))
        self._export_csv.setText(tr("export_csv_btn"))
        self._export_json.setText(tr("export_json_btn"))
        self._export_xlsx.setText(tr("export_xlsx_btn"))
        self._quality_filter.setItemText(0, tr("filter_all"))

    def add_row(self, result: Result) -> None:
        """Добавляет результат в таблицу. Инициализирует колонки при первом непустом result.data."""
        self._results.append(result)
        if not self._table.columnCount() or (result.data and self._columns == ["item", "status"]):
            self._columns = get_columns(self._results)
            self._table.setColumnCount(len(self._columns))
            self._table.setHorizontalHeaderLabels(self._columns)

        row = self._table.rowCount()
        self._table.insertRow(row)
        row_data = {"item": result.item, "status": result.status.value}
        row_data.update(result.data)
        for col_idx, col in enumerate(self._columns):
            item = QTableWidgetItem(str(row_data.get(col, "")))
            item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, col_idx, item)

        if any(r.data.get("quality") for r in self._results):
            self._quality_filter.setVisible(True)
        self._export_csv.setVisible(True)
        self._export_json.setVisible(True)
        self._export_xlsx.setVisible(True)
        self._update_total()

    def _update_total(self) -> None:
        total = sum(r.data.get("total_usd", 0) for r in self._results if r.data)
        if total > 0:
            self._total_label.setText(tr("total_fmt").format(total=total))
            self._total_label.setVisible(True)
        else:
            self._total_label.setVisible(False)

    def snapshot(self) -> list[Result]:
        """Возвращает копию текущих результатов для последующего восстановления."""
        return list(self._results)

    def restore(self, results: list[Result]) -> None:
        """Очищает таблицу и воспроизводит сохранённые результаты."""
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
