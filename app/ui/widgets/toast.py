from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, Property
from PySide6.QtWidgets import QLabel, QWidget, QGraphicsOpacityEffect


class Toast(QLabel):
    """Всплывающее уведомление, исчезающее через несколько секунд."""

    def __init__(
        self,
        text: str,
        level: str = "info",
        duration_ms: int = 3000,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("toast")
        self.setProperty("level", level)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setFixedHeight(44)
        self.setMinimumWidth(280)
        self.adjustSize()

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        # Fade in
        self._fade_in = QPropertyAnimation(self._opacity, b"opacity")
        self._fade_in.setDuration(250)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.OutCubic)

        # Fade out → destroy
        self._fade_out = QPropertyAnimation(self._opacity, b"opacity")
        self._fade_out.setDuration(400)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.InCubic)
        self._fade_out.finished.connect(self.deleteLater)

        # Auto-dismiss timer
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(duration_ms)
        self._timer.timeout.connect(self._fade_out.start)

    def show_toast(self) -> None:
        """Показать toast. Вызывать после добавления в layout."""
        self.show()
        self._fade_in.start()
        self._timer.start()


def show_toast(
    parent: QWidget,
    text: str,
    level: str = "info",
    duration_ms: int = 3000,
) -> Toast:
    """Утилита: создаёт и показывает toast поверх parent."""
    toast = Toast(text, level=level, duration_ms=duration_ms, parent=parent)
    # Position at top center
    toast.setFixedWidth(min(400, parent.width() - 40))
    toast.move((parent.width() - toast.width()) // 2, 12)
    toast.show_toast()
    return toast
