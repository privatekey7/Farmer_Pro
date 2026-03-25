# Relay

An alternative bridge provider. Used in Collector as a fallback or primary option (configurable via `bridge_provider`).

## Endpoints used

| Endpoint | Purpose |
|----------|---------|
| `GET /chains` | List of supported networks |
| `POST /quote` | Get a bridge quote |
| `GET /status` | Transaction status |

## Differences from LI.FI

- Status polling: every **5 seconds** (LI.FI — 15 sec)
- Quotes via POST, not GET
- Supports `gas_on_destination` — gas on the destination network is included in the bridge

## Fee address

The Relay fee address is stored in obfuscated form (XOR encoding). This protects it from accidental exposure in logs.

## Getting a quote

```python
quote = await relay_client.get_quote(
    from_chain=1,
    to_chain=42161,
    from_amount="500000000000000000",  # 0.5 ETH in wei
    from_address="0x...",
    to_address="0x..."
)
```

{% hint style="info" %}
Relay specialises in bridging native tokens (ETH → ETH on another network). For token swaps before bridging, use LI.FI.
{% endhint %}
