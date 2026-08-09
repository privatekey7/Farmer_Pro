from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    Qt, Signal, QSize, QPropertyAnimation, QEasingCurve, Property,
)
from PySide6.QtGui import QIcon, QColor, QPainter
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QLabel, QSizePolicy,
)

from app.core.base_module import BaseModule
from app.i18n import tr, i18n

_ICONS_DIR = Path(__file__).parent / "theme" / "icons"

# Map module name → icon filename (without extension)
_MODULE_ICON: dict[str, str] = {
    "EVM Balance":      "evm",
    "SVM Balance":      "svm",
    "Collector":        "collector",
    "Proxy Check":      "proxy",
    "Twitter Checker":  "twitter",
    "Discord Checker":  "discord",
}


class _SidebarButton(QPushButton):
    """One sidebar button: icon + text + status indicator."""

    def __init__(self, module: BaseModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.module = module
        self.setCheckable(True)
        self.setObjectName("sidebarButton")
        self.setCursor(Qt.PointingHandCursor)

        icon_file = _ICONS_DIR / f"{_MODULE_ICON.get(module.name, 'evm')}.svg"
        if icon_file.exists():
            self.setIcon(QIcon(str(icon_file)))
            self.setIconSize(QSize(22, 22))

        self.setText(module.name)
        self._status: str = ""  # "", "running", "done", "error"
        self._progress: float = 0.0   # 0.0..1.0, -1 = нет прогресса
        self._has_progress: bool = False

    def set_status(self, status: str) -> None:
        self._status = status
        self.setProperty("runStatus", status)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_progress(self, done: int, total: int) -> None:
        """Показывает полосу прогресса внизу кнопки. total<=0 — прогресс скрыт."""
        if total and total > 0:
            frac = max(0.0, min(1.0, done / total))
            # Перерисовываем только при заметном изменении (экономия CPU на UI-потоке)
            if self._has_progress and abs(frac - self._progress) < 0.005 and frac < 1.0:
                return
            self._progress = frac
            self._has_progress = True
        else:
            if not self._has_progress:
                return
            self._progress = 0.0
            self._has_progress = False
        self.update()

    def clear_progress(self) -> None:
        self.set_progress(0, 0)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._has_progress:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            h = 3
            y = self.height() - h - 2
            x0, w = 8, max(0, self.width() - 16)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 38))
            painter.drawRoundedRect(x0, y, w, h, 1.5, 1.5)
            fill = int(w * self._progress)
            if fill > 0:
                color = QColor("#30D158") if self._progress >= 1.0 else QColor("#0A84FF")
                painter.setBrush(color)
                painter.drawRoundedRect(x0, y, fill, h, 1.5, 1.5)
            painter.end()
        if not self._status:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        colors = {
            "running": QColor("#F0C040"),
            "done":    QColor("#30D158"),
            "error":   QColor("#FF453A"),
        }
        color = colors.get(self._status)
        if color:
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            x = self.width() - 14
            y = 8
            painter.drawEllipse(x, y, 8, 8)
        painter.end()


class Sidebar(QWidget):
    """Vertical sidebar with module icons, collapse animation, and status badges."""

    module_selected = Signal(object)  # BaseModule
    EXPANDED_WIDTH = 180
    COLLAPSED_WIDTH = 56

    def __init__(self, modules: list[BaseModule], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self._modules = modules
        self._buttons: list[_SidebarButton] = []
        self._expanded = True

        self.setFixedWidth(self.EXPANDED_WIDTH)
        self.setMinimumHeight(200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(4)

        # Logo / title
        self._title = QLabel("FarmerPro")
        self._title.setObjectName("sidebarTitle")
        self._title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._title)
        layout.addSpacing(12)

        # Module buttons
        for module in modules:
            btn = _SidebarButton(module, self)
            btn.clicked.connect(lambda checked, m=module, b=btn: self._on_clicked(m, b))
            self._buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch()

        # Collapse toggle
        self._toggle_btn = QPushButton("◀")
        self._toggle_btn.setObjectName("sidebarToggle")
        self._toggle_btn.setFixedSize(32, 32)
        self._toggle_btn.setCursor(Qt.PointingHandCursor)
        self._toggle_btn.clicked.connect(self._toggle_collapse)
        layout.addWidget(self._toggle_btn, alignment=Qt.AlignCenter)

        # Select first module
        if self._buttons:
            self._buttons[0].setChecked(True)

        # Animation
        self._anim = QPropertyAnimation(self, b"fixedWidth")
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)

    def _on_clicked(self, module: BaseModule, btn: _SidebarButton) -> None:
        for b in self._buttons:
            if b is not btn:
                b.setChecked(False)
        btn.setChecked(True)
        self.module_selected.emit(module)

    def _toggle_collapse(self) -> None:
        self._expanded = not self._expanded
        target = self.EXPANDED_WIDTH if self._expanded else self.COLLAPSED_WIDTH
        self._anim.setStartValue(self.width())
        self._anim.setEndValue(target)
        self._anim.start()
        self._toggle_btn.setText("◀" if self._expanded else "▶")
        for btn in self._buttons:
            btn.setText(btn.module.name if self._expanded else "")
        self._title.setVisible(self._expanded)

    def set_module_status(self, module: BaseModule, status: str) -> None:
        """Set module status: 'running', 'done', 'error', '' (clear)."""
        for btn in self._buttons:
            if btn.module is module:
                btn.set_status(status)
                break

    def set_module_progress(self, module: BaseModule, done: int, total: int) -> None:
        """Живой прогресс модуля в сайдбаре (вызывается из UI-потока)."""
        for btn in self._buttons:
            if btn.module is module:
                btn.set_progress(done, total)
                break

    def clear_module_progress(self, module: BaseModule | None = None) -> None:
        """Сбрасывает прогресс одного модуля или всех сразу."""
        for btn in self._buttons:
            if module is None or btn.module is module:
                btn.clear_progress()

    def select_module(self, index: int) -> None:
        """Programmatically select a module by index."""
        if 0 <= index < len(self._buttons):
            self._buttons[index].click()

    # Animated fixedWidth property for QPropertyAnimation
    def _get_fixed_width(self) -> int:
        return self.width()

    def _set_fixed_width(self, w: int) -> None:
        self.setFixedWidth(w)

    fixedWidth = Property(int, _get_fixed_width, _set_fixed_width)
