# LI.FI

Основной провайдер для cross-chain операций в Collector. Поддерживает swap токенов внутри одной цепи и bridging между разными сетями.

## Используемые эндпоинты

| Эндпоинт | Назначение |
|---------|-----------|
| `GET /chains` | Список поддерживаемых сетей |
| `GET /quote` | Получение квоты на swap или bridge |
| `GET /status` | Статус выполненной транзакции |
| `GET /gas/prices` | Текущие газовые цены |
| `GET /gas/suggestion/{chainId}` | Рекомендованный gas для bridge |

## Получение квоты

```python
quote = await lifi_client.get_quote(
    from_chain=1,           # Ethereum
    to_chain=42161,         # Arbitrum
    from_token="USDC",
    to_token="ETH",
    from_amount="1000000",  # в минимальных единицах (wei/units)
    from_address="0x...",
    slippage=0.005
)
```

Квота содержит:
- `transactionRequest` — готовые данные для подписи (to, data, value, gasLimit)
- `estimate.toAmount` — ожидаемое количество токенов на выходе
- `estimate.approvalAddress` — адрес для approve (если нужен)

## Polling статуса

После отправки транзакции Collector опрашивает `/status` каждые 15 секунд:

```python
status = await lifi_client.get_status(tx_hash, from_chain, to_chain)
# "PENDING" | "DONE" | "FAILED" | "NOT_FOUND"
```

Таймаут ожидания: **30 минут**. Если статус не `DONE` — транзакция помечается как `failed`.

## Ошибки

| Код | Действие |
|-----|---------|
| 400 Bad Request | Retry 3 раза, затем SKIP |
| 404 Not Found | Маршрут недоступен — SKIP |
| 429 Rate Limit | Экспоненциальная задержка |
| 5xx | Retry с задержкой |
