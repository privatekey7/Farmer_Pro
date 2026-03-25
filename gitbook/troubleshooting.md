# Troubleshooting

## Collector

### Module fails to start: "No supported networks"

**Cause:** Both LI.FI `/chains` and Relay `/chains` returned errors during initialization.

**Solution:**
1. Check your internet connection
2. Make sure your proxies are working — run Proxy Checker first
3. Try again later — the external APIs may be temporarily unavailable

---

### "Wallets X, subaccounts Y"

**Cause:** The `send_to_exchange` checkbox is enabled, but the number of lines in the two files does not match.

**Solution:** Make sure `wallets.txt` and `subaccounts.txt` have the same number of lines. Collector assigns subaccounts by index (1st wallet → 1st subaccount).

---

### Bridge stuck at PENDING status

Collector polls the bridge status for up to **30 minutes**. If the status hasn't moved to `DONE` within that time, the transaction is marked as `FAILED` and the exchange transfer step is skipped.

To verify the transaction manually, use the hash from the `bridge_tx` column.

---

### "balance too low" error in logs

The wallet doesn't have enough native token to cover gas, or the amount after deducting gas dropped to ≤ 0. The module skips the bridge step for that wallet.

---

## EVM Balance Checker

### Few results / empty balances

**Cause:** DeBank API may temporarily fail to return data under heavy load.

**Solution:**
- Reduce `Concurrency` to 5–10
- Use proxies to distribute requests
- Retries are automatic (up to 10 attempts, 3-second pause)

---

## SVM Balance Checker

### "Rate limit exceeded" on Solana RPC

The public RPC `api.mainnet-beta.solana.com` is heavily rate-limited.

**Solution:** Enter a private RPC (Helius, QuickNode, Triton) in the "RPC URL" field.

---

## Proxy Checker

### All proxies show `low`

Datacenter proxies (AWS, DO, Hetzner) are almost always flagged as `low`. For `high` quality, you need **residential** [proxies](https://dashboard.travchisproxies.com/billing/aff.php?aff=1488).

---

## Twitter / Discord Checker

### Mass `error` / `INVALID` results

Requests are being blocked by IP — proxies are either not configured or are datacenter proxies.

**Solution:** Proxies are required. Use residential [proxies](https://dashboard.travchisproxies.com/billing/aff.php?aff=1488) — datacenters are aggressively blocked by Twitter and Discord.

---

## General Issues

### UI is unresponsive while a module is running

This is normal — processing runs in a background thread. The UI updates as results come in. To stop, click **Stop** — the module will finish its current iteration and exit.

---

### App won't launch on Windows

Make sure the following are installed:
- Python 3.11+ (from the official installer, not Microsoft Store)
- Microsoft Visual C++ Redistributable (x64)
- All dependencies: `pip install -r requirements.txt`

{% hint style="info" %}
If you see `No module named 'PySide6'`, run `pip install PySide6` inside the activated virtual environment.
{% endhint %}
