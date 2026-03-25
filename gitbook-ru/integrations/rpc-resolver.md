# RPC Resolver

Автоматически выбирает рабочий RPC для заданного `chain_id`. Используется всеми модулями, которым нужно взаимодействие с EVM-блокчейном (отправка транзакций, проверка балансов, polling статуса).

## Цепочка fallback

RPC выбирается по приоритету: первый доступный из четырёх источников:

```
1. Publicnode    — https://ethereum.publicnode.com (100+ сетей)
2. LI.FI         — RPC из /chains (те, что LI.FI использует сам)
3. Relay         — RPC из /chains
4. ChainList     — https://chainlist.org (резервный список)
```

## Использование

```python
w3 = await rpc_resolver.get_web3(chain_id=1)
balance = w3.eth.get_balance("0x...")
```

Если ни один RPC недоступен — выбрасывается исключение с указанием `chain_id`.

## ChainList fallback

`chainlist_client.py` загружает список RPC из трёх источников параллельно:
1. chainlist.org API
2. GitHub mirrors
3. Локальный кэш (`relay_chains.json`, `rpc.txt`)

Результат кэшируется на время сессии, чтобы не делать повторные HTTP-запросы.

## Добавление своих RPC

Положи файл `rpc.txt` в корень проекта — один RPC на строку. Эти RPC будут использоваться с наивысшим приоритетом для соответствующих сетей.

```
# rpc.txt
https://mainnet.infura.io/v3/YOUR_KEY
https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY
```
