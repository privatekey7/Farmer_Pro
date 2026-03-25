# Обзор архитектуры

## Структура проекта

```
app/
├── core/           # Ядро: конфиг, модели, BaseModule, TaskRunner
├── modules/        # Бизнес-логика каждого модуля
├── integrations/   # Клиенты внешних API
├── storage/        # Парсинг входных данных и экспорт результатов
└── ui/             # PySide6-интерфейс
```

## Компоненты ядра

### Config (`app/core/config.py`)
Синглтон конфигурации. Загружает переменные из `.env` при инициализации. Доступен через `Config()` из любого места приложения.

### BaseModule (`app/core/base_module.py`)
Абстрактный класс, который должен реализовать каждый модуль:

```python
class BaseModule(ABC):
    @abstractmethod
    def get_config_widget(self) -> QWidget: ...

    @abstractmethod
    async def run(self, ctx: RunContext) -> AsyncGenerator[Result, None]: ...

    def stop(self) -> None: ...
```

### ModuleRegistry (`app/core/module_registry.py`)
Реестр модулей. `main.py` регистрирует все модули, `MainWindow` строит по ним вкладки.

### TaskRunner (`app/core/task_runner.py`)
`QThread`, который запускает `asyncio` event loop. Принимает модуль и `RunContext`, запускает `module.run()`, передаёт `Result`-ы в UI через Qt-сигналы:

| Сигнал | Тип | Назначение |
|--------|-----|-----------|
| `on_result` | `Result` | Новая строка в таблице |
| `on_log` | `str` | Сообщение в лог-панель |
| `on_finished` | — | Модуль завершил работу |

### Logger (`app/core/logger.py`)
Пишет в файл и в UI одновременно. Автоматически маскирует приватные ключи и длинные токены в логах (заменяет на `***`).

## Модель данных

```python
@dataclass
class Result:
    item: str              # адрес / токен / прокси
    status: ResultStatus   # OK | ERROR | SKIP
    data: dict[str, Any]   # колонки таблицы результатов
    error: str | None      # сообщение об ошибке

@dataclass
class RunContext:
    items: list[str]
    proxies: list[ProxyConfig]
    rpc_urls: list[str]
    concurrency: int
    extra: dict[str, Any]  # module-specific settings
```

## Поток данных

```
UI (main thread)
    │
    │  Нажатие "Старт"
    ▼
TaskRunner (QThread)
    │
    │  asyncio.run(module.run(ctx))
    ▼
Module.run() ──► Integration clients ──► External APIs
    │
    │  yield Result
    ▼
Qt signals ──► MainWindow ──► ResultsTable / LogWidget
```

## Прокси-ротация

`ProxyRotator` в `app/integrations/proxy_utils.py` раздаёт прокси по round-robin с thread-safe локом. Каждый конкурентный воркер получает следующий прокси в очереди.
