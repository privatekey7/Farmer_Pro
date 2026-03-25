# Жизненный цикл модуля

## Полный цикл выполнения

```
1. Пользователь настраивает параметры в config-виджете модуля
2. Нажимает кнопку "Старт"
3. MainWindow собирает RunContext из виджета
4. TaskRunner запускается в отдельном QThread
5. Внутри QThread запускается asyncio event loop
6. module.run(ctx) выполняется как async-генератор
7. Каждый yield Result → сигнал on_result → строка в таблице
8. Лог-сообщения → сигнал on_log → запись в LogWidget
9. Завершение / ошибка → сигнал on_finished → UI разблокируется
10. Пользователь экспортирует результаты
```

## Остановка по требованию

```python
# В модуле
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

`MainWindow` вызывает `module.stop()` при нажатии "Стоп". Модуль завершает текущую итерацию и выходит из цикла.

## Паттерн конкурентности

Большинство модулей используют `asyncio.Semaphore` для ограничения параллелизма:

```python
async def run(self, ctx: RunContext):
    sem = asyncio.Semaphore(ctx.concurrency)
    tasks = [self._process(item, sem) for item in ctx.items]
    async for result in async_generator_from_tasks(tasks):
        yield result
```

Исключение — **Collector**: транзакции выполняются строго последовательно (один кошелёк за раз), чтобы не создавать нonce-коллизий.

## Реализация нового модуля

Минимальный шаблон:

```python
from app.core.base_module import BaseModule
from app.core.models import Result, ResultStatus, RunContext

class MyModule(BaseModule):
    name = "My Module"
    description = "Что делает модуль"

    def get_config_widget(self) -> QWidget:
        # Вернуть виджет с настройками
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
        # Бизнес-логика
        ...
```

Зарегистрировать в `app/main.py`:

```python
registry.register(MyModule())
```
