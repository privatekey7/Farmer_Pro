from __future__ import annotations
import os
from pathlib import Path
from typing import Any
import yaml
from dotenv import load_dotenv
from .exceptions import ConfigError

_DEFAULT_CONFIG = Path(__file__).parent.parent / "resources" / "config.yaml"


class Config:
    _instance: Config | None = None

    def __init__(self, config_path: str | None = None):
        load_dotenv()
        path = Path(config_path) if config_path else _DEFAULT_CONFIG
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")
        with open(path, encoding="utf-8") as f:
            self._data: dict[str, Any] = yaml.safe_load(f) or {}
        Config._instance = self

    @classmethod
    def instance(cls) -> "Config":
        """Возвращает синглтон. Создаёт с дефолтным конфигом если ещё не инициализирован."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: object) -> None:
        """Обновляет значение в памяти и сохраняет config.yaml."""
        self._data[key] = value
        with open(_DEFAULT_CONFIG, "w", encoding="utf-8") as f:
            yaml.dump(self._data, f, allow_unicode=True, default_flow_style=False)

    def env(self, key: str, default: str | None = None) -> str | None:
        """Читает переменную окружения."""
        return os.environ.get(key, default)
