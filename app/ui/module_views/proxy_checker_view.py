from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog,
)
from app.core.models import ProxyConfig
from app.storage.parsers import parse_proxies
from app.i18n import tr, i18n


class ProxyCheckerConfigWidget(QWidget):
    """Панель настроек модуля Proxy Checker: выбор файла прокси."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._proxies: list[ProxyConfig] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        proxy_row = QHBoxLayout()
        self._lbl_proxy = QLabel()
        proxy_row.addWidget(self._lbl_proxy)
        self._proxy_path = QLineEdit()
        self._proxy_path.setPlaceholderText("proxies.txt")
        self._proxy_path.setReadOnly(True)
        proxy_row.addWidget(self._proxy_path)
        self._browse_btn = QPushButton()
        self._browse_btn.clicked.connect(self._browse_proxies)
        proxy_row.addWidget(self._browse_btn)
        layout.addLayout(proxy_row)

        self._proxy_status = QLabel(tr("file_not_loaded"))
        layout.addWidget(self._proxy_status)

        layout.addStretch()

        self.retranslate_ui()
        i18n.language_changed.connect(self.retranslate_ui)

    def retranslate_ui(self) -> None:
        self._lbl_proxy.setText(tr("proxy_label"))
        self._browse_btn.setText(tr("browse_btn"))
        if not self._proxies:
            self._proxy_status.setText(tr("file_not_loaded"))

    def _browse_proxies(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("select_proxy_file"), "", "Text files (*.txt);;All files (*)"
        )
        if not path:
            return
        try:
            self._proxies = parse_proxies(path)
            self._proxy_path.setText(path)
            self._proxy_status.setText(tr("n_proxies_loaded").format(n=len(self._proxies)))
        except Exception as e:
            self._proxies = []
            self._proxy_status.setText(tr("error_fmt").format(e=e))

    def get_proxies(self) -> list[ProxyConfig]:
        """Возвращает список прокси."""
        return self._proxies
