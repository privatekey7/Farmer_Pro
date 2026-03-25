from __future__ import annotations
from abc import ABC, abstractmethod
from typing import AsyncIterator, TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget
    from .models import RunContext, Result


class BaseModule(ABC):
    """Базовый интерфейс для всех модулей FarmerPro."""

    name: str  # Отображаемое имя в боковом меню

    @abstractmethod
    def get_config_widget(self) -> "QWidget | None":
        """
        Возвращает Qt-виджет настроек этого модуля.
        Возвращает None если у модуля нет настроек.
        """

    @abstractmethod
    def run(self, ctx: "RunContext") -> "AsyncIterator[Result]":
        """
        Возвращает async-генератор результатов.
        TaskRunner итерирует: async for result in module.run(ctx).
        """

    @abstractmethod
    async def stop(self) -> None:
        """
        Устанавливает внутренний флаг отмены.
        """

    def get_item_count(self) -> int:
        """Возвращает общее количество элементов для обработки. 0 = неизвестно."""
        return 0
