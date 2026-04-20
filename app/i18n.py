from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class I18n(QObject):
    language_changed = Signal()

    _instance: "I18n | None" = None

    def __new__(cls) -> "I18n":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return
        super().__init__()
        self._initialized = True
        self._lang = "en"
        self._translations: dict[str, dict[str, str]] = {}

    def load(self, lang: str, data: dict[str, str]) -> None:
        self._translations[lang] = data

    def set_language(self, lang: str) -> None:
        if lang != self._lang:
            self._lang = lang
            self.language_changed.emit()

    @property
    def language(self) -> str:
        return self._lang

    def tr(self, key: str) -> str:
        return self._translations.get(self._lang, {}).get(key, key)


i18n = I18n()


def tr(key: str) -> str:
    return i18n.tr(key)
