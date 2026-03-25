from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QToolButton, QWidget

from app.core.base_module import BaseModule


class SegmentedModuleTabs(QWidget):
    module_selected = Signal(object)  # BaseModule

    def __init__(self, modules: list[BaseModule], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._modules = list(modules)
        self._buttons: list[QToolButton] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(self._on_id_clicked)

        self.setProperty("segmented", True)

        for idx, module in enumerate(self._modules):
            btn = QToolButton(self)
            btn.setText(module.name)
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setProperty("segmentedItem", True)
            self._group.addButton(btn, idx)
            self._buttons.append(btn)
            layout.addWidget(btn)

        if self._buttons:
            self._buttons[0].setChecked(True)

    def set_current_index(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._buttons):
            return
        self._buttons[idx].setChecked(True)
        self.module_selected.emit(self._modules[idx])

    def current_module(self) -> BaseModule | None:
        checked_id = self._group.checkedId()
        if checked_id < 0:
            return None
        if checked_id >= len(self._modules):
            return None
        return self._modules[checked_id]

    def _on_id_clicked(self, idx: int) -> None:
        if 0 <= idx < len(self._modules):
            self.module_selected.emit(self._modules[idx])
