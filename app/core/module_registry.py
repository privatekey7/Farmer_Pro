from __future__ import annotations
from .base_module import BaseModule


class ModuleRegistry:
    """Реестр зарегистрированных модулей. Регистрация явная, в main.py."""

    def __init__(self) -> None:
        self._modules: list[BaseModule] = []

    def register(self, module: BaseModule) -> None:
        """Регистрирует модуль в реестре."""
        self._modules.append(module)

    def get_modules(self) -> list[BaseModule]:
        """Возвращает все зарегистрированные модули в порядке регистрации."""
        return list(self._modules)

    def get_by_name(self, name: str) -> BaseModule | None:
        """Ищет модуль по имени. Возвращает None если не найден."""
        for m in self._modules:
            if m.name == name:
                return m
        return None
