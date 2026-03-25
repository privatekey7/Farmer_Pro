# Architecture Overview

## Project structure

```
app/
├── core/           # Core: config, models, BaseModule, TaskRunner
├── modules/        # Business logic for each module
├── integrations/   # External API clients
├── storage/        # Input parsing and result export
└── ui/             # PySide6 interface
```

## Core components

### Config (`app/core/config.py`)
Configuration singleton. Loads variables from `.env` on initialization. Accessible via `Config()` from anywhere in the application.

### BaseModule (`app/core/base_module.py`)
Abstract class that every module must implement:

```python
class BaseModule(ABC):
    @abstractmethod
    def get_config_widget(self) -> QWidget: ...

    @abstractmethod
    async def run(self, ctx: RunContext) -> AsyncGenerator[Result, None]: ...

    def stop(self) -> None: ...
```

### ModuleRegistry (`app/core/module_registry.py`)
Module registry. `main.py` registers all modules; `MainWindow` builds tabs from them.

### TaskRunner (`app/core/task_runner.py`)
A `QThread` that runs an `asyncio` event loop. Takes a module and a `RunContext`, executes `module.run()`, and passes `Result` objects to the UI via Qt signals:

| Signal | Type | Purpose |
|--------|------|---------|
| `on_result` | `Result` | New row in the results table |
| `on_log` | `str` | Message in the log panel |
| `on_finished` | — | Module finished execution |

### Logger (`app/core/logger.py`)
Writes to a file and to the UI simultaneously. Automatically masks private keys and long tokens in logs (replaces them with `***`).

## Data model

```python
@dataclass
class Result:
    item: str              # address / token / proxy
    status: ResultStatus   # OK | ERROR | SKIP
    data: dict[str, Any]   # results table columns
    error: str | None      # error message

@dataclass
class RunContext:
    items: list[str]
    proxies: list[ProxyConfig]
    rpc_urls: list[str]
    concurrency: int
    extra: dict[str, Any]  # module-specific settings
```

## Data flow

```
UI (main thread)
    │
    │  "Start" button clicked
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

## Proxy rotation

`ProxyRotator` in `app/integrations/proxy_utils.py` distributes proxies in round-robin fashion with a thread-safe lock. Each concurrent worker receives the next proxy in the queue.
