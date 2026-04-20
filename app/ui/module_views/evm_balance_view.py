from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QFileDialog, QCheckBox, QComboBox, QPushButton,
)
from app.ui.widgets.drop_zone import DropZone
from app.core.models import ProxyConfig
from app.storage.parsers import parse_wallets, parse_proxies
from app.i18n import tr, i18n


class EvmBalanceConfigWidget(QWidget):
    """Панель настроек модуля EVM Balance: файлы + секция экспорта."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._wallets: list[str] = []
        self._proxies: list[ProxyConfig] = []
        self._export_results: list = []
        self._export_details: dict = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        # --- Кошельки ---
        self._wallet_drop = DropZone(
            label=tr("wallets_label"),
            placeholder="wallets.txt",
        )
        self._wallet_drop.file_dropped.connect(self._on_wallet_file)
        layout.addWidget(self._wallet_drop)

        # --- Прокси ---
        self._proxy_drop = DropZone(
            label=tr("proxy_label"),
            placeholder="proxy.txt",
        )
        self._proxy_drop.file_dropped.connect(self._on_proxy_file)
        layout.addWidget(self._proxy_drop)

        # --- Секция экспорта (выключена до завершения проверки) ---
        self._export_group = QGroupBox()
        self._export_group.setEnabled(False)
        export_layout = QVBoxLayout(self._export_group)

        # Галочки секций (Summary экспортируется из таблицы результатов слева)
        cb_row = QHBoxLayout()
        self._cb_tokens = QCheckBox()
        for cb in (self._cb_tokens,):
            cb.setChecked(True)
            cb_row.addWidget(cb)
        export_layout.addLayout(cb_row)

        # Фильтр токенов — выпадающий список (заполняется после проверки)
        token_row = QHBoxLayout()
        self._lbl_tokens_filter = QLabel()
        token_row.addWidget(self._lbl_tokens_filter)
        self._filter_tokens = QComboBox()
        self._filter_tokens.setEditable(False)
        self._filter_tokens.setMinimumWidth(160)
        token_row.addWidget(self._filter_tokens)
        export_layout.addLayout(token_row)

        # Кнопки экспорта
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
        self._wallet_drop.set_label(tr("wallets_label"))
        self._proxy_drop.set_label(tr("proxy_label"))
        self._export_group.setTitle(tr("export_group"))
        self._cb_tokens.setText(tr("tokens_checkbox"))
        self._lbl_tokens_filter.setText(tr("tokens_filter_label"))

    # --- Загрузка файлов ---

    def _on_wallet_file(self, path: str) -> None:
        try:
            parsed = parse_wallets(path)
            self._wallets = [w["raw"] for w in parsed if w["type"] == "address"]
            self._wallet_drop.set_status(tr("n_addresses_loaded").format(n=len(self._wallets)))
        except Exception as e:
            self._wallets = []
            self._wallet_drop.set_status(tr("error_fmt").format(e=e))

    def _on_proxy_file(self, path: str) -> None:
        try:
            self._proxies = parse_proxies(path)
            self._proxy_drop.set_status(tr("n_proxies_loaded").format(n=len(self._proxies)))
        except Exception as e:
            self._proxies = []
            self._proxy_drop.set_status(tr("error_fmt").format(e=e))

    def get_wallets(self) -> list[str]:
        return self._wallets

    def get_proxies(self) -> list[ProxyConfig]:
        return self._proxies

    # --- Экспорт ---

    def on_run_complete(self, results: list, details: dict) -> None:
        """Слот: вызывается Qt через queued connection после завершения run()."""
        self._export_results = results
        self._export_details = details
        self._export_group.setEnabled(True)
        # Собираем уникальные токены из кошельков для выпадающего списка
        token_keys: set[tuple[str, str]] = set()
        for addr_detail in details.values():
            for t in addr_detail.get("tokens_data", []):
                sym = (t.get("symbol") or "?").strip()
                ch = (t.get("chain") or "?").strip()
                if sym and ch:
                    token_keys.add((sym, ch))
        self._filter_tokens.clear()
        self._filter_tokens.addItem(tr("all_tokens"), None)
        for sym, ch in sorted(token_keys):
            display = f"{sym} ({ch.capitalize()})"
            value = f"{sym}:{ch}"
            self._filter_tokens.addItem(display, value)

    def _build_config(self):
        from app.storage.evm_exporter import EvmExportConfig
        token_filter = self._filter_tokens.currentData()
        return EvmExportConfig(
            summary=False,
            tokens=self._cb_tokens.isChecked(),
            token_filter=token_filter,
        )

    def _on_export(self, fmt: str) -> None:
        if not self._export_results:
            return
        config = self._build_config()
        extensions = {
            "csv":  (tr("save_csv"),  "evm_results.csv",  "CSV (*.csv)"),
            "json": (tr("save_json"), "evm_results.json", "JSON (*.json)"),
            "xlsx": (tr("save_xlsx"), "evm_results.xlsx", "Excel (*.xlsx)"),
        }
        title, default_name, file_filter = extensions[fmt]
        path, _ = QFileDialog.getSaveFileName(self, title, default_name, file_filter)
        if not path:
            return
        from app.storage.evm_exporter import EvmExporter
        EvmExporter().export(self._export_results, self._export_details, config, path, fmt)
