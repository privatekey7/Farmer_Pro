from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel


class EmptyState(QWidget):
    """Виджет пустого состояния — иконка + заголовок + подсказка."""

    def __init__(
        self,
        icon_text: str = "📋",
        title: str = "",
        subtitle: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("emptyState")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(8)

        self._icon = QLabel(icon_text)
        self._icon.setObjectName("emptyStateIcon")
        self._icon.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._icon)

        self._title = QLabel(title)
        self._title.setObjectName("emptyStateTitle")
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setWordWrap(True)
        layout.addWidget(self._title)

        self._subtitle = QLabel(subtitle)
        self._subtitle.setObjectName("emptyStateSubtitle")
        self._subtitle.setAlignment(Qt.AlignCenter)
        self._subtitle.setWordWrap(True)
        layout.addWidget(self._subtitle)

    def set_texts(self, icon: str = "", title: str = "", subtitle: str = "") -> None:
        if icon:
            self._icon.setText(icon)
        if title:
            self._title.setText(title)
        if subtitle:
            self._subtitle.setText(subtitle)
