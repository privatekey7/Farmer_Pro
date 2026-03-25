# SVM Balance Checker

Checks wallet balances on Solana — SOL and all SPL tokens.

## Requirements

- A file with Solana addresses (base58 format, do not start with `0x`)
- A [proxy](https://dashboard.travchisproxies.com/billing/aff.php?aff=1488) file (required)
- A Solana RPC server URL

{% hint style="warning" %}
The free public Solana RPC (`api.mainnet-beta.solana.com`) may be unstable. If checks fail, try a private RPC (Helius, QuickNode, Triton).
{% endhint %}

## How to Run

1. Open the **SVM Balance Checker** tab
2. Load your wallets file
3. Load your proxy file
4. Paste your RPC server address in the **RPC URL** field
5. Click **Start**

## Output Columns

| Column | Description |
|--------|-------------|
| `address` | Solana wallet address |
| `sol_balance` | SOL balance |
| `sol_usd` | SOL value in USD |
| `tokens` | Number of SPL tokens |
| `top_tokens` | Most valuable tokens |
