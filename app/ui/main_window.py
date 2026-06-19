from __future__ import annotations

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QPushButton, QStackedWidget, QProgressBar,
)
from PySide6.QtCore import Qt, QTimer, QUrl, QSettings
from PySide6.QtGui import QDesktopServices, QShortcut, QKeySequence

from app.core.base_module import BaseModule
from app.core.module_registry import ModuleRegistry
from app.core.models import RunContext, Result
from app.core.logger import Logger
from app.core.task_runner import TaskRunner
from app.ui.log_widget import LogWidget
from app.ui.results_table import ResultsTable
from app.ui.sidebar import Sidebar
from app.ui.widgets.toast import show_toast
from app.i18n import i18n, tr


class MainWindow(QMainWindow):
    def __init__(self, registry: ModuleRegistry) -> None:
        super().__init__()
        self.setWindowTitle("FarmerPro")
        self.setMinimumSize(1200, 700)
        self.setWindowState(self.windowState() | Qt.WindowMaximized)

        self._registry = registry
        self._task_runner = TaskRunner(self)
        self._task_runner.on_result.connect(self._on_result)
        self._task_runner.on_log.connect(self._on_log)
        self._task_runner.on_finished.connect(self._on_finished)

        self._current_module: BaseModule | None = None
        self._total_count: int = 0
        self._done_count: int = 0
        self._config_widgets: dict[int, QWidget] = {}
        self._results_cache: dict[int, list] = {}

        self._settings = QSettings("FarmerPro", "FarmerPro")

        # ── Sidebar ──
        self._sidebar = Sidebar(registry.get_modules())
        self._sidebar.module_selected.connect(self._on_module_selected)

        # ── Top toolbar (simplified — FAQ + language only) ──
        # Language toggle
        self._lang_en_btn = QPushButton("EN")
        self._lang_ru_btn = QPushButton("RU")
        self._lang_en_btn.setCheckable(True)
        self._lang_ru_btn.setCheckable(True)
        self._lang_en_btn.setProperty("langToggle", True)
        self._lang_ru_btn.setProperty("langToggle", True)
        self._lang_en_btn.setFixedWidth(36)
        self._lang_ru_btn.setFixedWidth(36)
        self._lang_en_btn.clicked.connect(lambda: self._set_lang("en"))
        self._lang_ru_btn.clicked.connect(lambda: self._set_lang("ru"))
        self._update_lang_buttons()

        # FAQ button
        self._faq_btn = QPushButton()
        self._faq_btn.setProperty("langToggle", True)
        self._faq_btn.setFixedWidth(46)
        self._faq_btn.clicked.connect(self._open_faq)

        top_bar = QWidget()
        top_bar.setObjectName("topToolbar")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(12, 8, 12, 8)
        top_layout.setSpacing(10)
        top_layout.addStretch()
        top_layout.addWidget(self._faq_btn)
        top_layout.addWidget(self._lang_en_btn)
        top_layout.addWidget(self._lang_ru_btn)

        # ── Progress (improved: 6px height, percentage) ──
        self._progress_bar = QProgressBar()
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.setObjectName("mainProgress")
        self._progress_label = QLabel("")
        self._progress_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._progress_label.setMinimumWidth(90)
        self._progress_label.setObjectName("progressLabel")

        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(8, 2, 8, 2)
        progress_row.addWidget(self._progress_bar)
        progress_row.addWidget(self._progress_label)

        self._progress_widget = QWidget()
        self._progress_widget.setLayout(progress_row)
        self._progress_widget.setVisible(False)

        # ── Module config stack (LEFT pane now) ──
        self._module_stack = QStackedWidget()
        self._module_stack.setObjectName("moduleStack")

        # ── Start / Stop buttons — sticky footer inside config pane ──
        self._start_btn = QPushButton()
        self._start_btn.setProperty("primary", True)
        self._stop_btn = QPushButton()
        self._stop_btn.setEnabled(False)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(12, 8, 12, 8)
        btn_row.setSpacing(8)
        btn_row.addWidget(self._start_btn, 1)
        btn_row.addWidget(self._stop_btn, 1)

        inspector_pane = QWidget()
        inspector_pane.setObjectName("inspectorPane")
        inspector_pane.setMinimumWidth(340)
        inspector_layout = QVBoxLayout(inspector_pane)
        inspector_layout.setContentsMargins(12, 12, 12, 0)
        inspector_layout.addWidget(self._module_stack, 1)
        inspector_layout.addLayout(btn_row)

        # ── Results & Log (RIGHT pane now) ──
        self._results_table = ResultsTable()
        self._log_widget = LogWidget()
        self._log_widget.setObjectName("logPane")

        results_pane = QWidget()
        results_pane.setObjectName("resultsPane")
        results_layout = QVBoxLayout(results_pane)
        results_layout.setContentsMargins(12, 12, 12, 12)
        results_layout.addWidget(self._results_table)

        # ── Layout: Config LEFT | Results RIGHT ──
        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.setObjectName("mainSplitter")
        main_splitter.addWidget(inspector_pane)
        main_splitter.addWidget(results_pane)
        main_splitter.setSizes([380, 720])

        # ── Log: collapsed by default ──
        self._log_widget.setMinimumHeight(36)
        self._log_collapsed = True

        self._log_toggle_btn = QPushButton()
        self._log_toggle_btn.setProperty("logFilter", True)
        self._log_toggle_btn.setFixedHeight(28)
        self._log_toggle_btn.clicked.connect(self._toggle_log)

        self._log_last_line = QLabel("")
        self._log_last_line.setObjectName("statsLabel")
        self._log_last_line.setStyleSheet("font-family: monospace; font-size: 9pt;")

        log_header = QHBoxLayout()
        log_header.setContentsMargins(8, 4, 8, 4)
        log_header.addWidget(self._log_toggle_btn)
        log_header.addWidget(self._log_last_line, 1)

        log_container = QWidget()
        log_container_layout = QVBoxLayout(log_container)
        log_container_layout.setContentsMargins(0, 0, 0, 0)
        log_container_layout.setSpacing(0)
        log_container_layout.addLayout(log_header)
        log_container_layout.addWidget(self._log_widget)
        self._log_widget.setVisible(False)

        self._vertical_splitter = QSplitter(Qt.Vertical)
        self._vertical_splitter.addWidget(main_splitter)
        self._vertical_splitter.addWidget(log_container)
        self._vertical_splitter.setSizes([660, 36])

        # ── Right content area ──
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(top_bar)
        content_layout.addWidget(self._progress_widget)
        content_layout.addWidget(self._vertical_splitter)

        # ── Root: Sidebar + Content ──
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._sidebar)
        root_layout.addWidget(content, 1)
        self.setCentralWidget(root)

        # ── Signals ──
        self._start_btn.clicked.connect(self._on_start)
        self._stop_btn.clicked.connect(self._on_stop)
        i18n.language_changed.connect(self.retranslate_ui)

        # ── Keyboard shortcuts ──
        for i in range(min(9, len(registry.get_modules()))):
            QShortcut(QKeySequence(f"Ctrl+{i+1}"), self).activated.connect(
                lambda idx=i: self._sidebar.select_module(idx)
            )
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self._on_start)
        QShortcut(QKeySequence("Escape"), self).activated.connect(self._on_stop)
        QShortcut(QKeySequence("Ctrl+E"), self).activated.connect(self._quick_export)

        # Initial translation
        self.retranslate_ui()

        # Restore splitter sizes
        self._restore_splitter_sizes()

        # Show first module
        if registry.get_modules():
            self._on_module_selected(registry.get_modules()[0])

    # ── Language ──

    def _set_lang(self, lang: str) -> None:
        from app.core.config import Config
        i18n.set_language(lang)
        Config.instance().set("language", lang)
        self._update_lang_buttons()

    def _update_lang_buttons(self) -> None:
        self._lang_en_btn.setChecked(i18n.language == "en")
        self._lang_ru_btn.setChecked(i18n.language == "ru")

    def retranslate_ui(self) -> None:
        self._start_btn.setText(tr("start_btn"))
        self._stop_btn.setText(tr("stop_btn"))
        self._faq_btn.setText(tr("faq_btn"))
        self._log_toggle_btn.setText(
            tr("log_expand_btn") if self._log_collapsed else tr("log_collapse_btn")
        )
        for widget in self._config_widgets.values():
            if hasattr(widget, "retranslate_ui"):
                widget.retranslate_ui()
        self._results_table.retranslate_ui()
        self._log_widget.retranslate_ui()

    def _open_faq(self) -> None:
        urls = {
            "en": "https://privatekey7.gitbook.io/farmerpro-en/",
            "ru": "https://privatekey7.gitbook.io/farmerpro-ru/",
        }
        QDesktopServices.openUrl(QUrl(urls.get(i18n.language, urls["en"])))

    # ── Log collapse / expand ──

    def _toggle_log(self) -> None:
        self._log_collapsed = not self._log_collapsed
        self._log_widget.setVisible(not self._log_collapsed)
        self._log_last_line.setVisible(self._log_collapsed)
        if self._log_collapsed:
            self._vertical_splitter.setSizes([660, 36])
            self._log_toggle_btn.setText(tr("log_expand_btn"))
        else:
            self._vertical_splitter.setSizes([480, 220])
            self._log_toggle_btn.setText(tr("log_collapse_btn"))

    # ── Splitter persistence ──

    def _restore_splitter_sizes(self) -> None:
        main_sizes = self._settings.value("mainSplitter")
        if main_sizes:
            try:
                splitter = self._vertical_splitter.widget(0)
                if isinstance(splitter, QSplitter):
                    splitter.setSizes([int(s) for s in main_sizes])
            except (ValueError, TypeError):
                pass

    def _save_splitter_sizes(self) -> None:
        splitter = self._vertical_splitter.widget(0)
        if isinstance(splitter, QSplitter):
            self._settings.setValue("mainSplitter", splitter.sizes())

    def closeEvent(self, event) -> None:
        self._save_splitter_sizes()
        super().closeEvent(event)

    # ── Module selection ──

    def _on_module_selected(self, module: BaseModule) -> None:
        if module is self._current_module:
            return
        if self._current_module is not None:
            self._results_cache[id(self._current_module)] = self._results_table.snapshot()
        self._current_module = module
        cached = self._results_cache.get(id(module))
        if cached:
            self._results_table.restore(cached)
        else:
            self._results_table.clear_results()
        key = id(module)
        widget = self._config_widgets.get(key)
        if widget is None:
            widget = module.get_config_widget()
            if widget is None:
                from app.ui.module_views.placeholder_view import PlaceholderView
                widget = PlaceholderView(module.name)
            self._config_widgets[key] = widget
            self._module_stack.addWidget(widget)
        self._module_stack.setCurrentWidget(widget)

    # ── Validation ──

    def _validate_start(self) -> bool:
        """Check that required inputs are loaded. Show toast on failure."""
        config_widget = self._config_widgets.get(id(self._current_module))
        if config_widget is None:
            return True

        # Modules that need wallets
        if hasattr(config_widget, "get_wallets"):
            wallets = config_widget.get_wallets()
            if not wallets:
                show_toast(self, tr("validation_no_wallets"), level="warning")
                return False

        # Modules that need tokens (Twitter/Discord)
        if hasattr(config_widget, "get_tokens"):
            tokens = config_widget.get_tokens()
            if not tokens:
                show_toast(self, tr("validation_no_tokens"), level="warning")
                return False

        # Proxy checker needs proxies
        if hasattr(config_widget, "get_proxies") and not hasattr(config_widget, "get_wallets") and not hasattr(config_widget, "get_tokens"):
            proxies = config_widget.get_proxies()
            if not proxies:
                show_toast(self, tr("validation_no_proxies"), level="warning")
                return False

        return True

    # ── Start / Stop ──

    def _on_start(self) -> None:
        if self._current_module is None:
            return
        if not self._start_btn.isEnabled():
            return
        if not self._validate_start():
            return

        self._results_table.clear_results()
        # Apply module column schema before any rows arrive
        schema = self._current_module.column_schema()
        if schema:
            self._results_table.set_schema(schema)
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

        self._sidebar.set_module_status(self._current_module, "running")

        self._done_count = 0
        self._total_count = self._current_module.get_item_count()
        if self._total_count > 0:
            self._progress_bar.setRange(0, self._total_count)
            self._progress_bar.setValue(0)
            self._progress_label.setText("0 / {0}  (0%)".format(self._total_count))
        else:
            self._progress_bar.setRange(0, 0)
            self._progress_label.setText("")
        self._progress_widget.setVisible(True)

        config_widget = self._config_widgets.get(id(self._current_module))
        proxies = (
            config_widget.get_proxies()
            if config_widget and hasattr(config_widget, "get_proxies")
            else []
        )
        ctx = RunContext(
            items=[],
            proxies=proxies,
            rpc_urls=[],
            concurrency=min(len(proxies), 50) if proxies else 10,
        )
        logger = Logger(on_log_signal=self._task_runner.on_log)
        ctx.extra["logger"] = logger
        self._task_runner.submit(self._current_module, ctx)

    def _on_stop(self) -> None:
        if self._stop_btn.isEnabled():
            self._task_runner.stop_module()

    def _on_result(self, result: Result) -> None:
        self._results_table.add_row(result)
        self._done_count += 1
        if self._total_count > 0:
            display = min(self._done_count, self._total_count)
            pct = int(display / self._total_count * 100)
            self._progress_bar.setValue(display)
            self._progress_label.setText(f"{display} / {self._total_count}  ({pct}%)")
            # Update sidebar with live count
            if self._current_module:
                self._sidebar.set_module_progress(
                    self._current_module, display, self._total_count
                )

    def _on_log(self, line: str) -> None:
        self._log_widget.append(line)
        # Update collapsed log preview
        if self._log_collapsed:
            truncated = line[:120] + "…" if len(line) > 120 else line
            self._log_last_line.setText(truncated)

    def _on_finished(self) -> None:
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

        cached = self._results_table.snapshot()
        has_errors = any(r.status.value == "error" for r in cached)
        status = "error" if has_errors else "done"

        if self._current_module:
            self._sidebar.set_module_status(self._current_module, status)

        if self._total_count > 0:
            self._progress_bar.setValue(self._total_count)
            pct = int(self._done_count / self._total_count * 100) if self._total_count else 100
            self._progress_label.setText(f"{self._done_count} / {self._total_count}  ({pct}%)")

        # Toast notification
        if has_errors:
            show_toast(self, tr("toast_finished_errors"), level="warning")
        else:
            show_toast(self, tr("toast_finished_ok"), level="success")

        # Don't auto-hide progress — keep it visible for reference

    def _quick_export(self) -> None:
        """Ctrl+E — trigger XLSX export from config panel if available."""
        config_widget = self._config_widgets.get(
            id(self._current_module) if self._current_module else None
        )
        if config_widget and hasattr(config_widget, "_on_export"):
            config_widget._on_export("xlsx")
