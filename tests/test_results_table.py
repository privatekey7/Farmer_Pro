# tests/test_results_table.py
"""Поведение ResultsTable.set_schema (Qt offscreen, без дисплея)."""
from __future__ import annotations
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.core.models import ColumnDef, Result, ResultStatus
from app.ui.results_table import ResultsTable

SCHEMA = [
    ColumnDef(key="item", label="Address", width=200),
    ColumnDef(key="status", label="Status"),
    ColumnDef(key="total_usd", label="Total $", fmt="${:.2f}", sort_type="numeric"),
    ColumnDef(key="speed", label="Speed", fmt="{:.0f} ms"),
    ColumnDef(key="hidden_col", label="Hidden", visible=False),
]


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def table(app):
    return ResultsTable()


def _result(address="0xabc", **data):
    return Result(item=address, status=ResultStatus.OK, data=data)


def test_set_schema_applies_columns_and_labels(table):
    table.set_schema(SCHEMA)
    headers = [table._table.horizontalHeaderItem(i).text()
               for i in range(table._table.columnCount())]
    assert headers == ["Address", "Status", "Total $", "Speed"]  # visible=False скрыта
    assert table._table.columnWidth(0) == 200


def test_add_row_uses_schema_order_and_format(table):
    table.set_schema(SCHEMA)
    table.add_row(_result(total_usd=380.4778, extra_key="ignored"))

    assert table._table.rowCount() == 1
    assert table._table.item(0, 0).text() == "0xabc"
    assert table._table.item(0, 1).text() == "ok"
    assert table._table.item(0, 2).text() == "$380.48"  # fmt применён
    # ключи вне схемы не создают колонок
    assert table._table.columnCount() == 4


def test_format_survives_non_numeric_value(table):
    table.set_schema(SCHEMA)
    table.add_row(_result(total_usd=1.0, speed="n/a"))
    assert table._table.item(0, 3).text() == "n/a"  # fmt не смог — значение как есть


def test_clear_results_keeps_schema_columns(table):
    table.set_schema(SCHEMA)
    table.add_row(_result(total_usd=1.0))
    table.clear_results()

    assert table._table.rowCount() == 0
    assert table._table.columnCount() == 4  # схема пережила очистку
    table.add_row(_result(total_usd=2.0))
    assert table._table.item(0, 2).text() == "$2.00"


def test_empty_schema_resets_to_legacy_autoderive(table):
    table.set_schema(SCHEMA)
    table.set_schema([])
    table.add_row(_result(foo="bar"))
    headers = [table._table.horizontalHeaderItem(i).text()
               for i in range(table._table.columnCount())]
    assert headers == ["item", "status", "foo"]  # legacy-поведение


def test_legacy_mode_without_schema_unchanged(table):
    table.add_row(_result(alpha=1))
    assert [table._table.horizontalHeaderItem(i).text()
            for i in range(table._table.columnCount())] == ["item", "status", "alpha"]
    table.clear_results()
    assert table._table.columnCount() == 0
