# Форматы входных данных

Все входные данные — текстовые файлы, одна запись на строку. Пустые строки и строки с `#` игнорируются.

## Кошельки (wallets.txt)

FarmerPro автоматически определяет тип записи:

| Тип | Формат | Пример |
|-----|--------|--------|
| Адрес | `0x` + 40 hex-символов | `0xAbCd...1234` |
| Приватный ключ | `0x` + 64 hex-символа | `0x4f3c...a1b2` |
| Мнемоника | 12 или 24 слова через пробел | `abandon abandon ... legal` |

{% hint style="info" %}
Для модуля **Collector** требуются приватные ключи или мнемоники — только адреса не подходят, так как модуль подписывает транзакции.
{% endhint %}

Деривация из мнемоники: `m/44'/60'/0'/0/0` (стандарт BIP44, первый адрес Ethereum).

## Прокси (proxies.txt)

Поддерживаются 5 форматов:

```
# Только host:port
192.168.1.1:8080

# host:port:user:pass
192.168.1.1:8080:myuser:mypass

# user:pass@host:port
myuser:mypass@192.168.1.1:8080

# SOCKS5 с аутентификацией
socks5://myuser:mypass@192.168.1.1:1080

# HTTP с протоколом
http://myuser:mypass@192.168.1.1:8080
```

{% hint style="danger" %}
Прокси обязательны для всех модулей. Без прокси сервисы (DeBank, Twitter, Discord, LI.FI) блокируют запросы по IP при массовом использовании. Для Twitter и Discord используй резидентные [прокси](https://dashboard.travchisproxies.com/billing/aff.php?aff=1488).
{% endhint %}

## RPC URLs

```
https://eth.drpc.org
https://rpc.ankr.com/eth
https://mainnet.infura.io/v3/YOUR_KEY
```

Используются как fallback при недоступности публичных RPC. Для Solana — отдельный список.

## Токены Twitter / Discord

Каждая строка — один токен аутентификации:

```
# Twitter: значение куки auth_token
abc123def456...

# Discord: токен бота или пользователя
MTIzNDU2Nzg5.AbCdEf.xyz...
```

## Субаккаунты биржи (Collector)

Адреса EVM получателей — один адрес на строку:

```
0xExchangeSubAccount1...
0xExchangeSubAccount2...
```

Collector назначает субаккаунты кошелькам по порядку (round-robin если субаккаунтов меньше, чем кошельков).
