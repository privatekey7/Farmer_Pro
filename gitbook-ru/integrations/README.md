# Интеграции

FarmerPro использует несколько внешних API и протоколов:

| Интеграция | Файл | Назначение |
|-----------|------|-----------|
| [DeBank](debank.md) | `debank_client.py` | Балансы EVM-кошельков |
| [LI.FI](lifi.md) | `lifi_client.py` | Cross-chain swaps и bridges |
| [Relay](relay.md) | `relay_client.py` | Альтернативный bridge-провайдер |
| [RPC Resolver](rpc-resolver.md) | `rpc_resolver.py` | Fallback-цепочка EVM RPC |
| [Pixelscan](pixelscan.md) | `pixelscan_client.py` | Проверка качества прокси |
| [DexScreener](dexscreener.md) | внутри `solana_rpc.py` | Цены токенов Solana |
| Twitter API | `twitter_client.py` | Чекер токенов X.com |
| Discord API | `discord_client.py` | Чекер токенов Discord |
