# Module Lifecycle

## Full execution cycle

```
1. User configures parameters in the module's config widget
2. Clicks "Start"
3. MainWindow assembles a RunContext from the widget
4. TaskRunner starts in a separate QThread
5. An asyncio event loop is started inside the QThread
6. module.run(ctx) executes as an async generator
7. Each yield Result → on_result signal → row in the results table
8. Log messages → on_log signal → entry in LogWidget
9. Completion / error → on_finished signal → UI is unlocked
10. User exports the results
```

## Stopping on demand

```python
# Inside the module
self._stop_event = threading.Event()

async def run(self, ctx):
    for item in ctx.items:
        if self._stop_event.is_set():
            return
        result = await self._process(item)
        yield result

def stop(self):
    self._stop_event.set()
```

`MainWindow` calls `module.stop()` when the "Stop" button is clicked. The module finishes the current iteration and exits the loop.

## Concurrency pattern

Most modules use `asyncio.Semaphore` to limit parallelism:

```python
async def run(self, ctx: RunContext):
    sem = asyncio.Semaphore(ctx.concurrency)
    tasks = [self._process(item, sem) for item in ctx.items]
    async for result in async_generator_from_tasks(tasks):
        yield result
```

The exception is **Collector**: transactions are executed strictly sequentially (one wallet at a time) to avoid nonce collisions.

## Implementing a new module

Minimal template:

```python
from app.core.base_module import BaseModule
from app.core.models import Result, ResultStatus, RunContext

class MyModule(BaseModule):
    name = "My Module"
    description = "What the module does"

    def get_config_widget(self) -> QWidget:
        # Return a widget with settings
        return MyConfigWidget()

    async def run(self, ctx: RunContext):
        for item in ctx.items:
            if self._stop_event.is_set():
                return
            try:
                data = await self._fetch(item)
                yield Result(item=item, status=ResultStatus.OK, data=data)
            except Exception as e:
                yield Result(item=item, status=ResultStatus.ERROR, error=str(e))

    async def _fetch(self, item: str) -> dict:
        # Business logic
        ...
```

Register in `app/main.py`:

```python
registry.register(MyModule())
```
