# DeBank API

Used to fetch EVM wallet balances — the full list of tokens across all supported networks.

## Usage in the project

- **EVM Balance Checker** — primary data source
- **Collector (Step 0)** — fetches the list of tokens for swapping

## Key method

```python
tokens = await debank_client.get_tokens(address)
# Returns: list[dict] with the following structure:
# {
#   "symbol": "USDC",
#   "chain": "eth",
#   "amount": 150.5,
#   "price": 1.0,
#   "usd_value": 150.5
# }
```

## Retry logic

- Maximum 10 attempts
- Delay between attempts: 3 seconds
- Uses the `@retry` decorator from `app/core/retry.py`

## Rate limiting

The DeBank API does not require an API key for basic requests. For bulk checks it is recommended to:
1. Use proxies (round-robin via `ProxyRotator`)
2. Limit concurrency to 10–20

{% hint style="info" %}
On HTTP 429 (Too Many Requests), the retry logic automatically increases the delay.
{% endhint %}
