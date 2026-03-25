from __future__ import annotations
from PySide6.QtWidgets import QListWidget, QListWidgetItem
from PySide6.QtCore import Signal
from app.core.base_module import BaseModule


class Sidebar(QListWidget):
    """Текстовое боковое меню модулей (~200px)."""

    module_selected = Signal(object)  # BaseModule

    def __init__(self, modules: list[BaseModule], parent=None) -> None:
        super().__init__(parent)
        self._modules = modules
        self.setFixedWidth(200)
        self._populate()
        self.itemClicked.connect(self._on_item_clicked)

    def _populate(self) -> None:
        for m in self._modules:
            item = QListWidgetItem(m.name)
            item.setData(256, m)  # Qt.UserRole = 256
            self.addItem(item)
        if self.count() > 0:
            self.setCurrentRow(0)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        module = item.data(256)
        if module:
            self.module_selected.emit(module)
