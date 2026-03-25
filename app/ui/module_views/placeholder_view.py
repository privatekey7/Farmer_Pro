from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
from PySide6.QtCore import Qt
from app.i18n import tr, i18n


class PlaceholderView(QWidget):
    """Заглушка панели настроек для модулей без реализации."""

    def __init__(self, module_name: str, parent=None) -> None:
        super().__init__(parent)
        self._module_name = module_name
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignCenter)
        layout = QVBoxLayout(self)
        layout.addWidget(self._label)
        i18n.language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self._label.setText(tr("placeholder_module_msg").format(module_name=self._module_name))
