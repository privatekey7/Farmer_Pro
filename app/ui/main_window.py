from __future__ import annotations
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QSplitter,
    QLabel,
    QPushButton,
    QStackedWidget,
    QProgressBar,
)
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices

from app.core.base_module import BaseModule
from app.core.module_registry import ModuleRegistry
from app.core.models import RunContext, Result
from app.core.logger import Logger
from app.core.task_runner import TaskRunner
from app.ui.log_widget import LogWidget
from app.ui.results_table import ResultsTable
from app.ui.segmented_tabs import SegmentedModuleTabs
from app.i18n import i18n, tr


class MainWindow(QMainWindow):
    def __init__(self, registry: ModuleRegistry) -> None:
        super().__init__()
        self.setWindowTitle("FarmerPro")
        self.setMinimumSize(1200, 700)
        self.setWindowState(self.windowState() | Qt.WindowMaximized)
        # Стили применяются глобально в app/main.py через app.ui.theme (Apple dark)

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

        # --- Toolbar (Apple-style) ---
        self._app_title = QLabel("FarmerPro")
        self._app_title.setObjectName("appTitle")

        self._start_btn = QPushButton()
        self._start_btn.setProperty("primary", True)
        self._stop_btn = QPushButton()
        self._stop_btn.setEnabled(False)

        self._tabs = SegmentedModuleTabs(registry.get_modules())
        self._tabs.module_selected.connect(self._on_module_selected)

        # Language toggle buttons (EN | RU)
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
        top_layout.setContentsMargins(12, 10, 12, 10)
        top_layout.setSpacing(10)

        # Left: Start / Stop
        top_layout.addWidget(self._start_btn)
        top_layout.addWidget(self._stop_btn)

        # Center tabs (true center via equal stretches)
        top_layout.addStretch(1)
        top_layout.addWidget(self._tabs, 0, Qt.AlignCenter)
        top_layout.addStretch(1)

        # Right: FAQ | EN | RU
        top_layout.addWidget(self._faq_btn)
        top_layout.addWidget(self._lang_en_btn)
        top_layout.addWidget(self._lang_ru_btn)

        # --- Progress row ---
        self._progress_bar = QProgressBar()
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(6)

        self._progress_label = QLabel("")
        self._progress_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._progress_label.setMinimumWidth(90)

        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(8, 2, 8, 2)
        progress_row.addWidget(self._progress_bar)
        progress_row.addWidget(self._progress_label)

        self._progress_widget = QWidget()
        self._progress_widget.setLayout(progress_row)
        self._progress_widget.setVisible(False)

        # --- Module config area (stacked) ---
        self._module_stack = QStackedWidget()
        self._module_stack.setObjectName("moduleStack")

        # --- Results & Log ---
        self._results_table = ResultsTable()
        self._log_widget = LogWidget()
        self._log_widget.setObjectName("logPane")

        results_pane = QWidget()
        results_pane.setObjectName("resultsPane")
        results_layout = QVBoxLayout(results_pane)
        results_layout.setContentsMargins(12, 12, 12, 12)
        results_layout.addWidget(self._results_table)

        inspector_pane = QWidget()
        inspector_pane.setObjectName("inspectorPane")
        inspector_pane.setMinimumWidth(340)
        inspector_layout = QVBoxLayout(inspector_pane)
        inspector_layout.setContentsMargins(12, 12, 12, 12)
        inspector_layout.addWidget(self._module_stack)

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(results_pane)
        main_splitter.addWidget(inspector_pane)
        main_splitter.setSizes([720, 380])

        self._log_widget.setMinimumHeight(140)
        vertical_splitter = QSplitter(Qt.Vertical)
        vertical_splitter.addWidget(main_splitter)
        vertical_splitter.addWidget(self._log_widget)
        vertical_splitter.setSizes([520, 180])

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(top_bar)
        root_layout.addWidget(self._progress_widget)
        root_layout.addWidget(vertical_splitter)
        self.setCentralWidget(root)

        # Signals
        self._start_btn.clicked.connect(self._on_start)
        self._stop_btn.clicked.connect(self._on_stop)
        i18n.language_changed.connect(self.retranslate_ui)

        # Initial translation
        self.retranslate_ui()

        # Показываем первый модуль
        if registry.get_modules():
            self._on_module_selected(registry.get_modules()[0])

    # ------------------------------------------------------------------
    # Language switching
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Module selection
    # ------------------------------------------------------------------

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

    def _on_start(self) -> None:
        if self._current_module is None:
            return
        from app.integrations.analytics import track
        track("module_started", {"module": self._current_module.name})
        self._results_table.clear_results()
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

        self._done_count = 0
        self._total_count = self._current_module.get_item_count()
        if self._total_count > 0:
            self._progress_bar.setRange(0, self._total_count)
            self._progress_bar.setValue(0)
            self._progress_label.setText(f"0 / {self._total_count}")
        else:
            self._progress_bar.setRange(0, 0)  # indeterminate pulsing
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
        self._task_runner.stop_module()

    def _on_result(self, result: Result) -> None:
        self._results_table.add_row(result)
        self._done_count += 1
        if self._total_count > 0:
            display = min(self._done_count, self._total_count)
            self._progress_bar.setValue(display)
            self._progress_label.setText(f"{display} / {self._total_count}")

    def _on_log(self, line: str) -> None:
        self._log_widget.append(line)

    def _on_finished(self) -> None:
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        if self._total_count > 0:
            self._progress_bar.setValue(self._total_count)
            self._progress_label.setText(f"{self._done_count} / {self._total_count}")
        QTimer.singleShot(1000, lambda: self._progress_widget.setVisible(False))
