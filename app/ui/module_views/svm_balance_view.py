from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QFileDialog,
    QComboBox,
)

from app.core.models import ProxyConfig, Result
from app.storage.parsers import parse_lines, parse_proxies
from app.i18n import tr, i18n

DEFAULT_RPC_URL = "https://api.mainnet-beta.solana.com"


class SvmBalanceConfigWidget(QWidget):
    """Config panel for the SVM Balance module: file inputs + export section."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._wallets: list[str] = []
        self._proxies: list[ProxyConfig] = []
        self._export_results: list[Result] = []
        self._export_details: dict = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        # --- Кошельки ---
        wallet_row = QHBoxLayout()
        self._lbl_wallets = QLabel()
        wallet_row.addWidget(self._lbl_wallets)
        self._wallet_path = QLineEdit()
        self._wallet_path.setPlaceholderText("wallets.txt")
        self._wallet_path.setReadOnly(True)
        wallet_row.addWidget(self._wallet_path)
        self._browse_wallets_btn = QPushButton()
        self._browse_wallets_btn.clicked.connect(self._browse_wallets)
        wallet_row.addWidget(self._browse_wallets_btn)
        layout.addLayout(wallet_row)

        self._wallet_status = QLabel(tr("file_not_loaded"))
        layout.addWidget(self._wallet_status)

        # --- Прокси ---
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

        # --- RPC URL ---
        self._lbl_rpc = QLabel()
        layout.addWidget(self._lbl_rpc)
        self._rpc_url_input = QLineEdit(DEFAULT_RPC_URL)
        layout.addWidget(self._rpc_url_input)

        # --- Экспорт ---
        self._export_group = QGroupBox()
        self._export_group.setEnabled(False)
        export_layout = QVBoxLayout(self._export_group)

        token_row = QHBoxLayout()
        self._lbl_tokens = QLabel()
        token_row.addWidget(self._lbl_tokens)
        self._filter_tokens = QComboBox()
        self._filter_tokens.setEditable(False)
        self._filter_tokens.setMinimumWidth(160)
        token_row.addWidget(self._filter_tokens)
        export_layout.addLayout(token_row)

        btn_row = QHBoxLayout()
        self._btn_csv  = QPushButton("CSV")
        self._btn_json = QPushButton("JSON")
        self._btn_xlsx = QPushButton("XLSX")
        self._btn_csv.clicked.connect(lambda: self._on_export("csv"))
        self._btn_json.clicked.connect(lambda: self._on_export("json"))
        self._btn_xlsx.clicked.connect(lambda: self._on_export("xlsx"))
        btn_row.addWidget(self._btn_csv)
        btn_row.addWidget(self._btn_json)
        btn_row.addWidget(self._btn_xlsx)
        export_layout.addLayout(btn_row)

        layout.addWidget(self._export_group)
        layout.addStretch()

        self.retranslate_ui()
        i18n.language_changed.connect(self.retranslate_ui)

    def retranslate_ui(self) -> None:
        self._lbl_wallets.setText(tr("wallets_label"))
        self._lbl_proxy.setText(tr("proxy_label"))
        self._browse_wallets_btn.setText(tr("browse_btn"))
        self._browse_proxies_btn.setText(tr("browse_btn"))
        self._lbl_rpc.setText(tr("rpc_url_label"))
        self._export_group.setTitle(tr("export_group"))
        self._lbl_tokens.setText(tr("tokens_filter_label"))
        if not self._wallets:
            self._wallet_status.setText(tr("file_not_loaded"))
        if not self._proxies:
            self._proxy_status.setText(tr("file_not_loaded"))

    # --- File loading ---

    def _browse_wallets(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("select_wallets_file"), "", "Text files (*.txt);;All files (*)"
        )
        if not path:
            return
        try:
            self._wallets = parse_lines(path)
            self._wallet_path.setText(path)
            self._wallet_status.setText(tr("n_addresses_loaded").format(n=len(self._wallets)))
        except Exception as e:
            self._wallets = []
            self._wallet_status.setText(tr("error_fmt").format(e=e))

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

    # --- Accessors ---

    def get_wallets(self) -> list[str]:
        return self._wallets

    def get_proxies(self) -> list[ProxyConfig]:
        return self._proxies

    def get_rpc_url(self) -> str:
        return self._rpc_url_input.text().strip() or DEFAULT_RPC_URL

    # --- Run complete slot ---

    def on_run_complete(self, results: list[Result], details: dict) -> None:
        """Called via Qt queued connection after module.run() finishes."""
        self._export_results = results
        self._export_details = details
        self._export_group.setEnabled(True)

        # Populate token filter combobox with unique symbols
        symbols: set[str] = set()
        for addr_detail in details.values():
            for t in addr_detail.get("tokens_data", []):
                sym = (t.get("symbol") or "").strip()
                if sym:
                    symbols.add(sym)

        self._filter_tokens.clear()
        self._filter_tokens.addItem(tr("all_tokens"), None)
        for sym in sorted(symbols):
            self._filter_tokens.addItem(sym, sym)

    # --- Export ---

    def _build_config(self):
        from app.storage.svm_exporter import SvmExportConfig
        return SvmExportConfig(
            summary=True,
            tokens=True,
            token_filter=self._filter_tokens.currentData(),
        )

    def _on_export(self, fmt: str) -> None:
        if not self._export_results:
            return
        config = self._build_config()
        extensions = {
            "csv":  (tr("save_csv"),  "svm_results.csv",  "CSV (*.csv)"),
            "json": (tr("save_json"), "svm_results.json", "JSON (*.json)"),
            "xlsx": (tr("save_xlsx"), "svm_results.xlsx", "Excel (*.xlsx)"),
        }
        title, default_name, file_filter = extensions[fmt]
        path, _ = QFileDialog.getSaveFileName(self, title, default_name, file_filter)
        if not path:
            return
        from app.storage.svm_exporter import SvmExporter
        SvmExporter().export(self._export_results, self._export_details, config, path, fmt)
