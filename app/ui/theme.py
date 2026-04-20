from __future__ import annotations

from pathlib import Path


def _theme_path() -> Path:
    return Path(__file__).resolve().parent / "theme" / "apple_dark.qss"


def _theme_dir_qss_path() -> str:
    # QSS стабильнее работает с абсолютными путями вида C:/... чем с file:///...
    return _theme_path().parent.resolve().as_posix()


def load_apple_dark_qss() -> str:
    qss = _theme_path().read_text(encoding="utf-8")
    return qss.replace("__THEME_DIR__", _theme_dir_qss_path())


def apply_apple_dark_theme(app) -> None:
    """
    Applies the global Apple-like dark theme to the QApplication.

    `app` is intentionally untyped here to avoid importing Qt in non-Qt contexts.
    """
    app.setStyleSheet(load_apple_dark_qss())
