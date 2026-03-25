# DeBank API

Используется для получения балансов EVM-кошельков — список всех токенов по всем поддерживаемым сетям.

## Использование в проекте

- **EVM Balance Checker** — основной источник данных
- **Collector (Шаг 0)** — получение списка токенов для свапа

## Ключевой метод

```python
tokens = await debank_client.get_tokens(address)
# Возвращает: list[dict] со структурой:
# {
#   "symbol": "USDC",
#   "chain": "eth",
#   "amount": 150.5,
#   "price": 1.0,
#   "usd_value": 150.5
# }
```

## Retry-логика

- Максимум 10 попыток
- Задержка между попытками: 3 секунды
- Используется декоратор `@retry` из `app/core/retry.py`

## Rate limiting

DeBank API не требует ключа для базовых запросов. При массовых проверках рекомендуется:
1. Использовать прокси (round-robin через `ProxyRotator`)
2. Ограничить concurrency до 10–20

{% hint style="info" %}
При получении HTTP 429 (Too Many Requests) retry-логика автоматически увеличивает задержку.
{% endhint %}
