# FarmerPro
<img width="1193" height="727" alt="Снимок экрана 2026-04-20 150801" src="https://github.com/user-attachments/assets/2bcfb31c-2c54-4203-9747-c21c440e6b54" />

**[English](#english) | [Русский](#русский)**

---

## English

A modular desktop tool for managing crypto accounts and wallets.

| Module | Description |
|--------|-------------|
| EVM Balance Checker | Check balances of EVM wallets across networks |
| SVM Balance Checker | Check balances of Solana wallets |
| Token Collector | Collect tokens from wallets via swap and bridge, then send them to an exchange |
| Proxy Checker | Check proxy availability and quality |
| Twitter Checker | Check Twitter/X account status |
| Discord Token Checker | Check Discord token status |

### What's new in v1.1.1 (26.09.2026)

- **EVM Balance** — fixed inflated Hyperliquid balances; DeFi positions on EVM chains (staking, lending, pools)
  are now counted and double-checked; far fewer `unverified` and errors.

### What's new in v1.1.0 (24.09.2026)

- **EVM Balance** — balances are double-checked on-chain, no more fake or inflated totals. New `unverified` status.
- **Collector** — safer swaps, scam-token protection, own proxy per wallet, smarter bridging.
  New settings: **Amount to exchange (% of balance)** and **Wallets at once**.
- **Export** — `TOTAL` row and a new **Operations** sheet with all Collector transactions.
- **Proxy / SVM / Twitter checkers** — several times faster.

Full list: [CHANGELOG.md](CHANGELOG.md)

### Installation

```bash
git clone https://github.com/privatekey7/Farmer_Pro.git
cd Farmer_Pro
pip install -r requirements.txt
python main.py
```

### Updating

```bash
git pull
pip install -r requirements.txt
```

For detailed documentation visit: https://privatekey7.gitbook.io/farmerpro-en/

---

## Русский

Модульный десктопный инструмент для работы с крипто-аккаунтами и кошельками.

| Модуль | Описание |
|--------|----------|
| EVM Balance Checker | Проверка балансов EVM-кошельков по сетям |
| SVM Balance Checker | Проверка балансов Solana-кошельков |
| Token Collector | Сбор токенов с кошельков через свап и бридж и отправка на биржу |
| Proxy Checker | Проверка работоспособности прокси |
| Twitter Checker | Проверка статуса Twitter/X аккаунтов |
| Discord Token Checker | Проверка статуса Discord токенов |

### Что нового в v1.1.1 (26.09.2026)

- **EVM Balance** — исправлены завышенные балансы Hyperliquid; DeFi-позиции в EVM-сетях (стейкинг, лендинг, пулы)
  теперь учитываются и перепроверяются; заметно меньше `unverified` и ошибок.

### Что нового в v1.1.0 (24.09.2026)

- **EVM Balance** — балансы перепроверяются в блокчейне, больше никаких фейковых или завышенных сумм. Новый статус `unverified`.
- **Collector** — безопасные свапы, защита от токенов-ловушек, свой прокси на каждый кошелёк, выгодные бриджи.
  Новые настройки: **Сумма на биржу (% от баланса)** и **Кошельков одновременно**.
- **Экспорт** — строка `TOTAL` и новый лист **Operations** со всеми транзакциями Collector.
- **Proxy / SVM / Twitter чекеры** — в несколько раз быстрее.

Полный список: [CHANGELOG.md](CHANGELOG.md)

### Установка

```bash
git clone https://github.com/privatekey7/Farmer_Pro.git
cd Farmer_Pro
pip install -r requirements.txt
python main.py
```

### Обновление

```bash
git pull
pip install -r requirements.txt
```

Подробная документация: https://privatekey7.gitbook.io/farmerpro-ru/
