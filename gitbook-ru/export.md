# Экспорт данных

Все модули поддерживают экспорт результатов через кнопки в UI.

## Форматы

{% tabs %}
{% tab title="CSV" %}
Стандартный CSV с заголовками. Открывается в Excel, Google Sheets, LibreOffice Calc.

```
address,total_usd,tokens_count,chains,top_tokens
0xAbCd...,1250.50,12,"eth,arb,op","USDC:eth,ETH:arb"
```
{% endtab %}

{% tab title="JSON" %}
Массив объектов. Удобен для дальнейшей обработки скриптами.

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
Excel-файл с форматированием. Поддерживает фильтры и сортировку в Excel.
{% endtab %}
{% endtabs %}

## Режимы экспорта EVM Balance Checker

| Режим | Описание |
|-------|---------|
| **Summary** | Одна строка на кошелёк: суммарный USD, количество токенов, список сетей |
| **Tokens** | Одна строка на каждый токен: symbol, chain, amount, usd_value |

### Фильтр токенов

В поле "Token filter" укажи `NAME:chain` для экспорта только нужных токенов:

```
USDC:eth        — только USDC в Ethereum
ETH:arb         — только ETH в Arbitrum
USDT            — USDT во всех сетях
```

## Куда сохраняются файлы

По умолчанию — диалог сохранения файла. Имя файла содержит название модуля и временную метку:

```
evm_balance_20260323_143022.csv
collector_results_20260323_143022.xlsx
```
