# LI.FI

The primary provider for cross-chain operations in the Collector. Supports token swaps within a single chain and bridging between different networks.

## Endpoints used

| Endpoint | Purpose |
|----------|---------|
| `GET /chains` | List of supported networks |
| `GET /quote` | Get a swap or bridge quote |
| `GET /status` | Status of a submitted transaction |
| `GET /gas/prices` | Current gas prices |
| `GET /gas/suggestion/{chainId}` | Recommended gas for a bridge |

## Getting a quote

```python
quote = await lifi_client.get_quote(
    from_chain=1,           # Ethereum
    to_chain=42161,         # Arbitrum
    from_token="USDC",
    to_token="ETH",
    from_amount="1000000",  # in minimum units (wei/units)
    from_address="0x...",
    slippage=0.005
)
```

The quote contains:
- `transactionRequest` — ready-to-sign transaction data (to, data, value, gasLimit)
- `estimate.toAmount` — expected output token amount
- `estimate.approvalAddress` — address to approve (if required)

## Status polling

After submitting the transaction, Collector polls `/status` every 15 seconds:

```python
status = await lifi_client.get_status(tx_hash, from_chain, to_chain)
# "PENDING" | "DONE" | "FAILED" | "NOT_FOUND"
```

Timeout: **30 minutes**. If status is not `DONE` — the transaction is marked as `failed`.

## Errors

| Code | Action |
|------|--------|
| 400 Bad Request | Retry 3 times, then SKIP |
| 404 Not Found | Route unavailable — SKIP |
| 429 Rate Limit | Exponential backoff |
| 5xx | Retry with delay |
