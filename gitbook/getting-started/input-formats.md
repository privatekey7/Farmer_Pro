# Input Formats

All input data consists of plain text files with one entry per line. Empty lines and lines starting with `#` are ignored.

## Wallets (wallets.txt)

FarmerPro automatically detects the entry type:

| Type | Format | Example |
|------|--------|---------|
| Address | `0x` + 40 hex characters | `0xAbCd...1234` |
| Private key | `0x` + 64 hex characters | `0x4f3c...a1b2` |
| Mnemonic | 12 or 24 space-separated words | `abandon abandon ... legal` |

{% hint style="info" %}
The **Collector** module requires private keys or mnemonics — addresses alone are not sufficient, as the module signs transactions.
{% endhint %}

Derivation from mnemonic: `m/44'/60'/0'/0/0` (BIP44 standard, first Ethereum address).

## Proxies (proxies.txt)

Five formats are supported:

```
# host:port only
192.168.1.1:8080

# host:port:user:pass
192.168.1.1:8080:myuser:mypass

# user:pass@host:port
myuser:mypass@192.168.1.1:8080

# SOCKS5 with authentication
socks5://myuser:mypass@192.168.1.1:1080

# HTTP with protocol
http://myuser:mypass@192.168.1.1:8080
```

{% hint style="danger" %}
Proxies are required for all modules. Without proxies, services (DeBank, Twitter, Discord, LI.FI) will block requests by IP during bulk use. Use residential [proxies](https://dashboard.travchisproxies.com/billing/aff.php?aff=1488) for Twitter and Discord.
{% endhint %}

## RPC URLs

```
https://eth.drpc.org
https://rpc.ankr.com/eth
https://mainnet.infura.io/v3/YOUR_KEY
```

Used as a fallback when public RPCs are unavailable. Solana uses a separate list.

## Twitter / Discord tokens

One authentication token per line:

```
# Twitter: value of the auth_token cookie
abc123def456...

# Discord: bot or user token
MTIzNDU2Nzg5.AbCdEf.xyz...
```

## Exchange sub-accounts (Collector)

EVM recipient addresses — one address per line:

```
0xExchangeSubAccount1...
0xExchangeSubAccount2...
```

Collector assigns sub-accounts to wallets in order (round-robin if there are fewer sub-accounts than wallets).
