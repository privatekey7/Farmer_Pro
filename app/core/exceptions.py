class FarmerProError(Exception):
    """Базовое исключение проекта."""


class RetryExhaustedError(FarmerProError):
    """Все попытки retry исчерпаны."""
    def __init__(self, attempts: int, last_error: Exception):
        super().__init__(f"Failed after {attempts} attempts: {last_error}")
        self.last_error = last_error


class ConfigError(FarmerProError):
    """Ошибка конфигурации."""


class ParseError(FarmerProError):
    """Ошибка парсинга входного файла."""
