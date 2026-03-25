# EVM Balance Checker

Checks wallet balances on Ethereum, Arbitrum, BNB Chain, Optimism, Base, and 90+ other compatible networks — all in one run.

## Requirements

- A wallet file (wallet addresses)
- A [proxy](https://dashboard.travchisproxies.com/billing/aff.php?aff=1488) file (required)

## How to Run

1. Open the **EVM Balance Checker** tab
2. Click **Browse** next to "Wallets" and select your file
3. Click **Browse** next to "Proxy" and select your proxy file
4. Click **Start**

Results appear in the table as wallets are checked.

## Output Columns

| Column | Description |
|--------|-------------|
| `address` | Wallet address |
| `total_usd` | Total value of everything in the wallet in USD |
| `tokens_count` | Number of unique tokens found |
| `chains` | Networks where assets are held |
| `top_tokens` | Most valuable tokens in the wallet |

## Saving Results

After completion, click **CSV**, **JSON**, or **XLSX** — choose the format and save location.

Two export modes:
- **Summary** — one row per wallet (good for a general overview)
- **Tokens** — one row per token (good for detailed analysis)

{% hint style="info" %}
Want to export only a specific token? The "Tokens" dropdown shows all tokens found across wallets — select the one you need to export only that token on a specific network.
{% endhint %}
