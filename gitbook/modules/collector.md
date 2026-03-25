# Collector

Automatically consolidates all assets from wallets and transfers them to a single location — for example, an exchange deposit address.

**What happens for each wallet:**
1. All non-native tokens (USDC, USDT, etc.) are swapped to the native token of the network (ETH, BNB, etc.)
2. If the wallet has tokens but not enough native gas, it bridges native tokens from an available network first
3. The native token is transferred to your target network (e.g., everything to Arbitrum)
4. Optionally — transferred to a specified exchange address

## Requirements

- A file with **private keys or mnemonics** (addresses alone won't work — the app needs to sign transactions)
- Residential [proxies](https://dashboard.travchisproxies.com/billing/aff.php?aff=1488) (required)
- A file with exchange addresses — if you want to send to subaccounts (optional)

## Settings

| Parameter | Description | Default |
|-----------|-------------|---------|
| **Min. token value** | Tokens cheaper than this are ignored | $0.03 |
| **Min. transfer amount** | If native balance is below this, transfer is skipped | $0.03 |
| **Exclude chains** | Selected chains will be skipped | — |
| **Target chains** | Where to transfer (choose one or more) | — |
| **Slippage** | Allowed price deviation during swap (%) | 3% |
| **Pause between wallets** | Random delay to avoid bot detection | 60–180 sec |
| **Send to exchange** | Enables the exchange transfer step | off |
| **Pause before sending** | Wait after bridging to the target network | 60 sec |

## How to Run

1. Open the **Collector** tab
2. Load your private keys file
3. Load your proxy file
4. Select **target chains** — where to consolidate tokens
5. Adjust other parameters as needed
6. To send to exchange — enable the checkbox and load the addresses file
7. Click **Start**

{% hint style="warning" %}
Wallets are processed one at a time, not in parallel. This is intentional — to prevent transactions from conflicting with each other. For a large number of wallets, this will take time.
{% endhint %}

## Output Columns

| Column | Description |
|--------|-------------|
| `address` | Wallet address |
| `chains_processed` | Networks where tokens were found |
| `chains_skipped` | Skipped networks and reason |
| `tokens_swapped` | Number of swaps completed |
| `total_collected_usd` | Total value in USD |
| `bridge_chain` | Source and destination of the bridge |
| `bridge_status` | Bridge result |
| `exchange_tx` | Exchange transfer transaction hash |

## After Completion

Click **CSV**, **JSON**, or **XLSX** to save results. Export buttons appear automatically when the run finishes.
