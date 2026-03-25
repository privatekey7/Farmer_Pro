from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QFileDialog,
    QComboBox,
)
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
        token_row = QHBoxLayout()
        self._lbl_tokens = QLabel()
        token_row.addWidget(self._lbl_tokens)
        self._token_path = QLineEdit()
        self._token_path.setPlaceholderText("tokens.txt")
        self._token_path.setReadOnly(True)
        token_row.addWidget(self._token_path)
        self._browse_tokens_btn = QPushButton()
        self._browse_tokens_btn.clicked.connect(self._browse_tokens)
        token_row.addWidget(self._browse_tokens_btn)
        layout.addLayout(token_row)

        self._token_status = QLabel(tr("file_not_loaded"))
        layout.addWidget(self._token_status)

        # --- Proxies ---
        proxy_row = QHBoxLayout()
        self._lbl_proxy = QLabel()
        proxy_row.addWidget(self._lbl_proxy)
        self._proxy_path = QLineEdit()
        self._proxy_path.setPlaceholderText("proxy.txt")
        self._proxy_path.setReadOnly(True)
        proxy_row.addWidget(self._proxy_path)
        self._browse_proxies_btn = QPushButton()
        self._browse_proxies_btn.clicked.connect(self._browse_proxies)
        proxy_row.addWidget(self._browse_proxies_btn)
        layout.addLayout(proxy_row)

        self._proxy_status = QLabel(tr("file_not_loaded"))
        layout.addWidget(self._proxy_status)

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
        self._lbl_tokens.setText(tr("tokens_label_checker"))
        self._lbl_proxy.setText(tr("proxy_label_checker"))
        self._browse_tokens_btn.setText(tr("browse_btn"))
        self._browse_proxies_btn.setText(tr("browse_btn"))
        self._export_group.setTitle(tr("export_group"))
        self._lbl_status.setText(tr("status_label"))
        self._lbl_format.setText(tr("format_label"))
        self._export_btn.setText(tr("export_btn"))
        self._status_combo.setItemText(0, tr("filter_all"))
        if not self._tokens:
            self._token_status.setText(tr("file_not_loaded"))
        if not self._proxies:
            self._proxy_status.setText(tr("file_not_loaded"))

    # --- File loading ---

    def _browse_tokens(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("select_tokens_file"), "", "Text files (*.txt);;All files (*)"
        )
        if not path:
            return
        try:
            self._tokens = parse_lines(path)
            self._token_path.setText(path)
            self._token_status.setText(tr("n_tokens_loaded").format(n=len(self._tokens)))
        except Exception as e:
            self._tokens = []
            self._token_status.setText(tr("error_fmt").format(e=e))

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
