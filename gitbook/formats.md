# File Formats

All files are plain text (.txt), one entry per line. Lines starting with `#` are ignored.

## Wallets

```
# Addresses
0xAbCd1234567890abcd1234567890AbCd12345678

# Private keys
0x4f3ca1b2d8e72f904f3ca1b2d8e72f904f3ca1b2d8e72f904f3ca1b2d8e72f90

# Mnemonic (12 or 24 words)
abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon legal
```

## Proxies

```
# Address:port only
192.168.1.1:8080

# Address:port:login:password
192.168.1.1:8080:myuser:mypass

# login:password@address:port
myuser:mypass@192.168.1.1:8080

# With explicit protocol
http://myuser:mypass@192.168.1.1:8080
socks5://myuser:mypass@192.168.1.1:1080
```

{% hint style="warning" %}
Proxy Checker only supports HTTP proxies. SOCKS5 is not supported.
{% endhint %}

## Twitter Tokens

```
# The auth_token cookie value from your browser
abc123def456ghi789jkl012mno345pqr678
xyz987wvu654tsr321qpo098nml765kji432
```

## Discord Tokens

```
# User or bot token
MTIzNDU2Nzg5MDEyMzQ1Njc4.AbCdEf.xyz123abc456def789
```

## Exchange Subaccounts (for Collector)

```
# EVM recipient addresses, one per line
0x1111111111111111111111111111111111111111
0x2222222222222222222222222222222222222222
0x3333333333333333333333333333333333333333
```

{% hint style="info" %}
Order matters: first wallet → first exchange address, second → second, etc. The number of lines must match.
{% endhint %}

