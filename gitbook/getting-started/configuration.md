# Configuration

## Environment Variables (.env)

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_LEVEL` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` | `INFO` |

Example `.env`:

```env
LOG_LEVEL=INFO
```

## Module Parameters

Each module is configured through its panel in the UI — a config widget to the right of the results table. Parameters are saved for the duration of the session.

### Common Parameters (all modules)

| Parameter | Description |
|-----------|-------------|
| Wallets / Items | Path to a text file or manual input |
| Proxies | Path to a proxy file (required) |

### Collector — Additional Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `min_token_usd` | Minimum token value for swap ($) | 0.03 |
| `min_bridge_usd` | Minimum amount for bridge ($) | 0.03 |
| `excluded_chains` | Chains to skip | [] |
| `target_chains` | Target chains (DeBank keys or names) | [] |
| `slippage` | Allowed price slippage (%) | 3.0 |
| `delay_min` | Minimum pause between wallets (sec) | 60 |
| `delay_max` | Maximum pause between wallets (sec) | 180 |
| `send_to_exchange` | Send to exchange subaccount | false |
| `delay_after_bridge` | Pause before sending to exchange (sec) | 60 |

## Logs

Logs are written to `logs/farmerpro.log` and displayed in the bottom panel of the UI in real time. Private keys and tokens are automatically masked.
