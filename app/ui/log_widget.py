from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
    QPushButton, QLineEdit, QStackedWidget,
)
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtCore import Qt

from app.ui.widgets.empty_state import EmptyState
from app.i18n import tr, i18n


class LogWidget(QWidget):
    """Виджет лога с цветовой индикацией, фильтрацией, поиском и freeze."""

    INFO_COLOR = "#8E8E93"
    WARN_COLOR = "#FFD60A"
    ERROR_COLOR = "#FF453A"
    SUCCESS_COLOR = "#30D158"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._all_lines: list[tuple[str, str]] = []  # (line, level)
        self._frozen = False
        self._active_filter: str = "all"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Controls row
        controls = QHBoxLayout()
        controls.setSpacing(4)

        self._btn_all = QPushButton("All")
        self._btn_info = QPushButton("Info")
        self._btn_warn = QPushButton("Warn")
        self._btn_error = QPushButton("Error")

        for btn, filt in [
            (self._btn_all, "all"),
            (self._btn_info, "info"),
            (self._btn_warn, "warn"),
            (self._btn_error, "error"),
        ]:
            btn.setCheckable(True)
            btn.setProperty("logFilter", True)
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda checked, f=filt: self._set_filter(f))
            controls.addWidget(btn)
        self._btn_all.setChecked(True)

        controls.addStretch()

        self._search = QLineEdit()
        self._search.setObjectName("logSearch")
        self._search.setFixedWidth(180)
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._refilter)
        controls.addWidget(self._search)

        self._freeze_btn = QPushButton("❄")
        self._freeze_btn.setCheckable(True)
        self._freeze_btn.setProperty("logFilter", True)
        self._freeze_btn.setFixedSize(26, 26)
        self._freeze_btn.setToolTip(tr("freeze_log_tooltip"))
        self._freeze_btn.toggled.connect(self._on_freeze)
        controls.addWidget(self._freeze_btn)

        self._clear_btn = QPushButton()
        self._clear_btn.setProperty("logFilter", True)
        self._clear_btn.setFixedHeight(26)
        self._clear_btn.clicked.connect(self._clear)
        controls.addWidget(self._clear_btn)

        layout.addLayout(controls)

        # Text display
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(5000)
        self._text.setObjectName("logText")

        # Empty state
        self._empty = EmptyState(
            icon_text="📝",
            title=tr("empty_log_title"),
            subtitle=tr("empty_log_subtitle"),
        )

        self._stack = QStackedWidget()
        self._stack.addWidget(self._empty)
        self._stack.addWidget(self._text)
        self._stack.setCurrentIndex(0)

        layout.addWidget(self._stack)

        self.retranslate_ui()
        i18n.language_changed.connect(self.retranslate_ui)

    def retranslate_ui(self) -> None:
        self._clear_btn.setText(tr("clear_log_btn"))
        self._search.setPlaceholderText(tr("search_placeholder"))
        self._freeze_btn.setToolTip(tr("freeze_log_tooltip"))
        self._empty.set_texts(
            title=tr("empty_log_title"),
            subtitle=tr("empty_log_subtitle"),
        )

    def append(self, line: str) -> None:
        level = "info"
        if "ERROR" in line:
            level = "error"
        elif "WARN" in line:
            level = "warn"
        elif "SUCCESS" in line or "✓" in line:
            level = "success"

        self._all_lines.append((line, level))
        self._stack.setCurrentIndex(1)

        if self._frozen:
            return

        search = self._search.text().strip().lower()
        if self._active_filter != "all" and level != self._active_filter:
            return
        if search and search not in line.lower():
            return

        self._append_colored(line, level)

    def _append_colored(self, line: str, level: str) -> None:
        colors = {
            "info":    self.INFO_COLOR,
            "warn":    self.WARN_COLOR,
            "error":   self.ERROR_COLOR,
            "success": self.SUCCESS_COLOR,
        }
        color = colors.get(level, self.INFO_COLOR)
        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.setCharFormat(fmt)
        cursor.insertText(line + "\n")
        self._text.setTextCursor(cursor)
        self._text.ensureCursorVisible()

    def _set_filter(self, filt: str) -> None:
        self._active_filter = filt
        for btn, f in [
            (self._btn_all, "all"),
            (self._btn_info, "info"),
            (self._btn_warn, "warn"),
            (self._btn_error, "error"),
        ]:
            btn.setChecked(f == filt)
        self._refilter()

    def _refilter(self) -> None:
        self._text.clear()
        search = self._search.text().strip().lower()
        for line, level in self._all_lines:
            if self._active_filter != "all" and level != self._active_filter:
                continue
            if search and search not in line.lower():
                continue
            self._append_colored(line, level)

    def _on_freeze(self, checked: bool) -> None:
        self._frozen = checked

    def _clear(self) -> None:
        self._all_lines.clear()
        self._text.clear()
        self._stack.setCurrentIndex(0)
