from __future__ import annotations
import logging
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
from app.core.config import Config
from app.core.module_registry import ModuleRegistry
from app.modules.evm_balance_checker import EvmBalanceCheckerModule
from app.modules.svm_balance_checker import SvmBalanceCheckerModule
from app.modules.token_collector import CollectorModule
from app.modules.proxy_checker import ProxyCheckerModule
from app.modules.twitter_checker import TwitterCheckerModule
from app.modules.discord_token_checker import DiscordTokenCheckerModule
from app.ui.main_window import MainWindow
from app.ui.theme import apply_apple_dark_theme
from app.integrations.analytics import track
from app.i18n import i18n
from app.resources.translations.en import TRANSLATIONS as EN
from app.resources.translations.ru import TRANSLATIONS as RU


def main() -> None:
    config = Config()  # инициализация синглтона
    i18n.load("en", EN)
    i18n.load("ru", RU)
    i18n._lang = config.get("language", "en")

    track("app_open")
    app = QApplication(sys.argv)
    apply_apple_dark_theme(app)
    icon_path = Path(__file__).parent / "resources" / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    registry = ModuleRegistry()
    registry.register(EvmBalanceCheckerModule())
    registry.register(SvmBalanceCheckerModule())
    registry.register(CollectorModule())
    registry.register(ProxyCheckerModule())
    registry.register(TwitterCheckerModule())
    registry.register(DiscordTokenCheckerModule())
    window = MainWindow(registry)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
