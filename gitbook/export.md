# Exporting Data

All modules support result export via buttons in the UI.

## Formats

{% tabs %}
{% tab title="CSV" %}
Standard CSV with headers. Opens in Excel, Google Sheets, LibreOffice Calc.

```
address,total_usd,tokens_count,chains,top_tokens
0xAbCd...,1250.50,12,"eth,arb,op","USDC:eth,ETH:arb"
```
{% endtab %}

{% tab title="JSON" %}
Array of objects. Convenient for further processing with scripts.

```json
[
  {
    "address": "0xAbCd...",
    "total_usd": 1250.50,
    "tokens_count": 12,
    "chains": ["eth", "arb", "op"]
  }
]
```
{% endtab %}

{% tab title="XLSX" %}
Formatted Excel file. Supports filters and sorting in Excel.
{% endtab %}
{% endtabs %}

## EVM Balance Checker Export Modes

| Mode | Description |
|------|-------------|
| **Summary** | One row per wallet: total USD, token count, chain list |
| **Tokens** | One row per token: symbol, chain, amount, usd_value |

### Token Filter

In the "Token filter" field, enter `NAME:chain` to export only specific tokens:

```
USDC:eth        — USDC on Ethereum only
ETH:arb         — ETH on Arbitrum only
USDT            — USDT on all chains
```

## Where Files Are Saved

By default, a save dialog appears. The file name includes the module name:

```
evm_results.csv
collector_results.xlsx
```
