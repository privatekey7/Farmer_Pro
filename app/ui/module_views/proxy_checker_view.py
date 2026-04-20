from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout
from app.ui.widgets.drop_zone import DropZone
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

        self._proxy_drop = DropZone(
            label=tr("proxy_label"),
            placeholder="proxies.txt",
        )
        self._proxy_drop.file_dropped.connect(self._on_proxy_file)
        layout.addWidget(self._proxy_drop)

        layout.addStretch()

        self.retranslate_ui()
        i18n.language_changed.connect(self.retranslate_ui)

    def retranslate_ui(self) -> None:
        self._proxy_drop.set_label(tr("proxy_label"))

    def _on_proxy_file(self, path: str) -> None:
        try:
            self._proxies = parse_proxies(path)
            self._proxy_drop.set_status(tr("n_proxies_loaded").format(n=len(self._proxies)))
        except Exception as e:
            self._proxies = []
            self._proxy_drop.set_status(tr("error_fmt").format(e=e))

    def get_proxies(self) -> list[ProxyConfig]:
        """Возвращает список прокси."""
        return self._proxies
