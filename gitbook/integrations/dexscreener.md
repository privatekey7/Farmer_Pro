# DexScreener

Used to fetch Solana token prices in the **SVM Balance Checker** module.

## Purpose

| Module | Role |
|--------|------|
| SVM Balance Checker | Fetches USD prices for SPL tokens and SOL to calculate `sol_usd` |

## How it works

`SolanaClient` requests prices in batches of up to 30 mint addresses at a time:

```
GET https://api.dexscreener.com/tokens/v1/solana/<mint1>,<mint2>,...
```

This includes the native SOL address (`So111...112`) to obtain the current SOL/USD rate.

## Notes

- Requests are batched (30 mint addresses per request)
- A local cache `app/data/solana_tokens.json` is used for well-known tokens
- If a price is not found for a token, it is set to `0.0` (the token is still included in the list without a USD value)

{% hint style="info" %}
DexScreener does not require an API key for basic price queries.
{% endhint %}
