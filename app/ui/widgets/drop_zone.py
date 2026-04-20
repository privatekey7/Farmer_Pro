from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QFileDialog,
)

from app.i18n import tr, i18n


class DropZone(QWidget):
    """Зона Drag & Drop + кнопка Browse. Заменяет стандартную строку выбора файла."""

    file_dropped = Signal(str)  # Путь к файлу

    def __init__(
        self,
        label: str = "",
        placeholder: str = "file.txt",
        file_filter: str = "Text files (*.txt);;All files (*)",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("dropZone")
        self._file_filter = file_filter
        self._file_path: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Row: Label + path + browse button
        row = QHBoxLayout()
        self._label = QLabel(label)
        self._label.setObjectName("dropZoneLabel")
        row.addWidget(self._label)

        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText(placeholder)
        self._path_edit.setReadOnly(True)
        row.addWidget(self._path_edit)

        self._browse_btn = QPushButton(tr("browse_btn"))
        self._browse_btn.clicked.connect(self._browse)
        row.addWidget(self._browse_btn)
        layout.addLayout(row)

        # Drop area (hidden by default, shown when no file loaded)
        self._drop_area = QWidget()
        self._drop_area.setObjectName("dropArea")
        self._drop_area.setFixedHeight(64)
        drop_layout = QVBoxLayout(self._drop_area)
        drop_layout.setAlignment(Qt.AlignCenter)
        self._drop_hint = QLabel(tr("drop_hint"))
        self._drop_hint.setObjectName("dropHint")
        self._drop_hint.setAlignment(Qt.AlignCenter)
        drop_layout.addWidget(self._drop_hint)
        layout.addWidget(self._drop_area)

        # Status
        self._status = QLabel(tr("file_not_loaded"))
        self._status.setObjectName("dropZoneStatus")
        layout.addWidget(self._status)

        i18n.language_changed.connect(self._retranslate)

    def _retranslate(self) -> None:
        self._browse_btn.setText(tr("browse_btn"))
        self._drop_hint.setText(tr("drop_hint"))
        if not self._file_path:
            self._status.setText(tr("file_not_loaded"))

    def set_label(self, text: str) -> None:
        self._label.setText(text)

    def set_status(self, text: str) -> None:
        self._status.setText(text)
        if text and "✓" in text:
            self._drop_area.setVisible(False)
        else:
            self._drop_area.setVisible(True)

    def get_file_path(self) -> str:
        return self._file_path

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("select_file"), "", self._file_filter,
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str) -> None:
        self._file_path = path
        self._path_edit.setText(path)
        self._drop_area.setVisible(False)
        self.file_dropped.emit(path)

    # --- Drag & Drop ---
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            self.setProperty("dragOver", True)
            self.style().unpolish(self)
            self.style().polish(self)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self.setProperty("dragOver", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event: QDropEvent) -> None:
        self.setProperty("dragOver", False)
        self.style().unpolish(self)
        self.style().polish(self)
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path and Path(path).is_file():
                self._set_file(path)
