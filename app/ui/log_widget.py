from __future__ import annotations
from PySide6.QtWidgets import QPlainTextEdit, QWidget, QVBoxLayout, QPushButton, QHBoxLayout
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtCore import Qt
from app.i18n import tr, i18n


class LogWidget(QWidget):
    """Виджет лога в реальном времени с цветовой индикацией уровней."""

    INFO_COLOR = "#aaaaaa"   # серый
    WARN_COLOR = "#f0c040"   # жёлтый
    ERROR_COLOR = "#f04040"  # красный

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(5000)

        self._clear_btn = QPushButton()
        self._clear_btn.clicked.connect(self._text.clear)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._text)
        layout.addWidget(self._clear_btn)

        self.retranslate_ui()
        i18n.language_changed.connect(self.retranslate_ui)

    def retranslate_ui(self) -> None:
        self._clear_btn.setText(tr("clear_log_btn"))

    def append(self, line: str) -> None:
        """Добавляет строку с цветом по уровню (INFO/WARN/ERROR)."""
        if "ERROR" in line:
            color = self.ERROR_COLOR
        elif "WARN" in line:
            color = self.WARN_COLOR
        else:
            color = self.INFO_COLOR

        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.setCharFormat(fmt)
        cursor.insertText(line + "\n")
        self._text.setTextCursor(cursor)
        self._text.ensureCursorVisible()
