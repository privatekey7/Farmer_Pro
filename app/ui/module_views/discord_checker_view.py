from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QFileDialog, QComboBox, QPushButton,
)
from app.ui.widgets.drop_zone import DropZone
from app.core.models import ProxyConfig
from app.storage.parsers import parse_proxies, parse_lines
from app.i18n import tr, i18n


class DiscordCheckerConfigWidget(QWidget):
    """Config panel for Discord Checker module."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tokens: list[str] = []
        self._proxies: list[ProxyConfig] = []
        self._export_results: list = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        # --- Tokens ---
        self._token_drop = DropZone(
            label=tr("tokens_label_checker"),
            placeholder="tokens.txt",
        )
        self._token_drop.file_dropped.connect(self._on_token_file)
        layout.addWidget(self._token_drop)

        # --- Proxies ---
        self._proxy_drop = DropZone(
            label=tr("proxy_label_checker"),
            placeholder="proxy.txt",
        )
        self._proxy_drop.file_dropped.connect(self._on_proxy_file)
        layout.addWidget(self._proxy_drop)

        # --- Export (disabled until run completes) ---
        self._export_group = QGroupBox()
        self._export_group.setEnabled(False)
        export_layout = QVBoxLayout(self._export_group)

        filter_row = QHBoxLayout()
        self._lbl_status = QLabel()
        filter_row.addWidget(self._lbl_status)
        self._status_combo = QComboBox()
        self._status_combo.addItem(tr("filter_all"), None)
        for s in ("ok", "invalid", "disabled"):
            self._status_combo.addItem(s, s)
        filter_row.addWidget(self._status_combo)
        filter_row.addSpacing(16)
        self._lbl_format = QLabel()
        filter_row.addWidget(self._lbl_format)
        self._fmt_combo = QComboBox()
        for fmt in ("xlsx", "csv", "json"):
            self._fmt_combo.addItem(fmt, fmt)
        filter_row.addWidget(self._fmt_combo)
        filter_row.addStretch()
        export_layout.addLayout(filter_row)

        self._export_btn = QPushButton()
        self._export_btn.clicked.connect(self._on_export)
        export_layout.addWidget(self._export_btn)

        layout.addWidget(self._export_group)
        layout.addStretch()

        self.retranslate_ui()
        i18n.language_changed.connect(self.retranslate_ui)

    def retranslate_ui(self) -> None:
        self._token_drop.set_label(tr("tokens_label_checker"))
        self._proxy_drop.set_label(tr("proxy_label_checker"))
        self._export_group.setTitle(tr("export_group"))
        self._lbl_status.setText(tr("status_label"))
        self._lbl_format.setText(tr("format_label"))
        self._export_btn.setText(tr("export_btn"))
        self._status_combo.setItemText(0, tr("filter_all"))

    # --- File loading ---

    def _on_token_file(self, path: str) -> None:
        try:
            self._tokens = parse_lines(path)
            self._token_drop.set_status(tr("n_tokens_loaded").format(n=len(self._tokens)))
        except Exception as e:
            self._tokens = []
            self._token_drop.set_status(tr("error_fmt").format(e=e))

    def _on_proxy_file(self, path: str) -> None:
        try:
            self._proxies = parse_proxies(path)
            self._proxy_drop.set_status(tr("n_proxies_loaded").format(n=len(self._proxies)))
        except Exception as e:
            self._proxies = []
            self._proxy_drop.set_status(tr("error_fmt").format(e=e))

    # --- Accessors (called by module) ---

    def get_tokens(self) -> list[str]:
        return self._tokens

    def get_proxies(self) -> list[ProxyConfig]:
        return self._proxies

    def on_run_complete(self, results: list) -> None:
        """Slot: called via Qt queued connection after run() finishes."""
        self._export_results = results
        self._export_group.setEnabled(True)

    # --- Export ---

    def _on_export(self) -> None:
        if not self._export_results:
            return
        from app.storage.discord_exporter import DiscordExporter, DiscordExportConfig

        status_filter = self._status_combo.currentData()
        fmt = self._fmt_combo.currentData()
        config = DiscordExportConfig(status_filter=status_filter)

        extensions = {
            "csv":  (tr("save_csv"),  "discord_results.csv",  "CSV (*.csv)"),
            "json": (tr("save_json"), "discord_results.json", "JSON (*.json)"),
            "xlsx": (tr("save_xlsx"), "discord_results.xlsx", "Excel (*.xlsx)"),
        }
        title, default_name, file_filter = extensions[fmt]
        path, _ = QFileDialog.getSaveFileName(self, title, default_name, file_filter)
        if not path:
            return
        DiscordExporter().export(self._export_results, config, path, fmt)
