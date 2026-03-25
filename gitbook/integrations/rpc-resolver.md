# RPC Resolver

Automatically selects a working RPC for a given `chain_id`. Used by all modules that need to interact with EVM blockchains (sending transactions, checking balances, polling status).

## Fallback chain

RPCs are selected by priority — the first available from four sources:

```
1. Publicnode    — https://ethereum.publicnode.com (100+ networks)
2. LI.FI         — RPCs from /chains (the ones LI.FI uses itself)
3. Relay         — RPCs from /chains
4. ChainList     — https://chainlist.org (last resort)
```

## Usage

```python
w3 = await rpc_resolver.get_web3(chain_id=1)
balance = w3.eth.get_balance("0x...")
```

If no RPC is available, an exception is raised with the relevant `chain_id`.

## ChainList fallback

`chainlist_client.py` loads the RPC list from three sources in parallel:
1. chainlist.org API
2. GitHub mirrors
3. Local cache (`relay_chains.json`, `rpc.txt`)

The result is cached for the session to avoid repeated HTTP requests.

## Adding custom RPCs

Place an `rpc.txt` file in the project root — one RPC per line. These RPCs will be used with the highest priority for the matching networks.

```
# rpc.txt
https://mainnet.infura.io/v3/YOUR_KEY
https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY
```
