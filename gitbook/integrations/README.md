# Integrations

FarmerPro uses several external APIs and protocols:

| Integration | File | Purpose |
|-------------|------|---------|
| [DeBank](debank.md) | `debank_client.py` | EVM wallet balances |
| [LI.FI](lifi.md) | `lifi_client.py` | Cross-chain swaps and bridges |
| [Relay](relay.md) | `relay_client.py` | Alternative bridge provider |
| [RPC Resolver](rpc-resolver.md) | `rpc_resolver.py` | EVM RPC fallback chain |
| [Pixelscan](pixelscan.md) | `pixelscan_client.py` | Proxy quality checking |
| [DexScreener](dexscreener.md) | inside `solana_rpc.py` | Solana token prices |
| Twitter API | `twitter_client.py` | X.com token checker |
| Discord API | `discord_client.py` | Discord token checker |
