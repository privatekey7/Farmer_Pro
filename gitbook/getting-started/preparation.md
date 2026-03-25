# Preparation

Before you start, you will need a few files. All of them are plain text files (.txt) with one entry per line.

## Wallet File

This is a list of wallet addresses or keys that you want to work with.

FarmerPro supports three formats:

**Address only** — suitable for balance checks:
```
0xAbCd1234...
0x9876Ef12...
```

**Private key** — required for Collector (to sign and send transactions):
```
0x4f3ca1b2...
0xd8e72f90...
```

**Mnemonic** (seed phrase of 12 or 24 words) — also works with Collector:
```
abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon legal
```

{% hint style="info" %}
Plain addresses are sufficient for balance checks. Collector requires private keys or mnemonics — the app needs to be able to sign transactions on behalf of the wallet.
{% endhint %}

## Proxy File

Proxies are intermediate servers through which the application makes requests. They are needed to prevent services from blocking you during bulk operations.

{% hint style="danger" %}
Proxies are required for all modules. Without them, services will block your IP after just a few requests. You can purchase proxies here: [proxies](https://dashboard.travchisproxies.com/billing/aff.php?aff=1488).
{% endhint %}

Supported proxy formats:

```
# Without login and password
192.168.1.1:8080

# With login and password
192.168.1.1:8080:myuser:mypass

# Alternative format
myuser:mypass@192.168.1.1:8080

# With protocol specified
http://myuser:mypass@192.168.1.1:8080
```

{% hint style="warning" %}
Use HTTP proxies only. SOCKS5 proxies are not supported by the Proxy Checker module. Twitter and Discord require residential proxies — they look like regular home connections and are less likely to be blocked.
{% endhint %}

## Twitter or Discord Token File

A token (or auth_token) is a special code that your browser uses to prove you are logged into an account. It can be found in your browser's cookies.

How to find your Twitter auth_token:
1. Log in to Twitter in your browser
2. Open the developer tools (F12)
3. Go to Application → Cookies
4. Find the cookie named `auth_token` — copy its value

The file looks like this — one token per line:
```
abc123def456ghi789...
xyz987wvu654...
```

For Discord, the token is found in the same place, or it can be obtained from the application settings.

## Exchange Sub-account File (Collector only)

This is a list of EVM addresses to which Collector will send the collected funds. These are typically exchange deposit addresses — one per wallet.

```
0xExchange111...
0xExchange222...
0xExchange333...
```

Collector matches them in order: first wallet → first exchange address, second wallet → second address, and so on.

{% hint style="info" %}
Lines starting with `#` are ignored in all files. Use this for comments or to temporarily exclude an entry.
{% endhint %}
