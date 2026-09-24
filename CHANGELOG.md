# Что нового / What's new

**[Русский](#русский) | [English](#english)**

---

## Русский

### Версия 1.1.0 — 24.09.2026

#### EVM Balance Checker — только честные балансы
- Балансы теперь **перепроверяются**: токены сверяются напрямую с блокчейном, а итог
  подтверждается двумя независимыми проверками через разные прокси. Больше никаких
  «чужих» или завышенных сумм.
- Позиции в **Hyperliquid, Lighter и Polymarket** берутся напрямую из этих сервисов.
- Новый статус **`unverified`** (оранжевый) — баланс не удалось надёжно подтвердить.
  Такой кошелёк не входит в общую сумму. Просто запустите проверку для него ещё раз.
- В таблице появились колонки **Tokens $** (токены) и **DeFi $** (позиции в протоколах).

#### Collector — надёжнее, безопаснее, быстрее
- **Своп только на нужную сумму** — программа больше не выдаёт токенам «бесконечное»
  разрешение на списание.
- **Защита от токенов-ловушек**: перед обменом своп проверяется «вхолостую». Токены,
  которые нельзя продать (honeypot, огромная комиссия), запоминаются и больше не трогаются.
- **Каждый кошелёк работает через свой прокси** — все запросы и транзакции. Кошельки
  не связываются между собой по IP. Нерабочие прокси отсеиваются автоматически.
- **Одна целевая сеть на кошелёк.** Если выбрано несколько целевых сетей, для каждого
  кошелька случайно выбирается одна — все средства собираются в неё.
- **Бридж выгоднее**: выбирается маршрут, после которого у вас останется больше денег.
  Бридж не делается, если комиссия съедает слишком большую часть суммы.
- Газ считается точнее — транзакции не «зависают» и не переплачивают.
- При обрыве связи транзакция безопасно отправляется повторно через другой сервер —
  без риска двойной отправки.
- **Новое: «Сумма на биржу (% от баланса)».** Можно отправлять не всё, а, например,
  случайно 90–95% с каждого кошелька. По умолчанию — 100% (как раньше).
- **Новое: «Кошельков одновременно»** (1–20). Ускоряет работу с большим списком.
  По умолчанию 1 — строго по очереди, как раньше.
- Если средства уже лежат в целевой сети, повторный запуск просто отправит их на биржу.
- Исправлен перевод на биржу, если адрес субаккаунта записан маленькими буквами.
- Исправлена работа в BNB Chain, Polygon и похожих сетях.
- Понятные итоги в таблице: **Swaps / Bridges** в виде «успешно/попыток»,
  **Swapped $**, **Bridged $**, **Refuel $**, **Target** (куда собраны средства).
- В логе — зелёные сообщения **Success** об успешных действиях и кнопка-фильтр для них.

#### Экспорт
- Строка **TOTAL** с итоговыми суммами (EVM, SVM, Collector).
- Collector: новый лист **Operations** — все отправленные транзакции с сетью, суммой,
  хэшем и статусом.
- Исправлены ошибки сохранения в CSV и XLSX у Collector.

#### Скорость
- **Proxy Checker**: 100 прокси проверяются примерно за 9 секунд (раньше ~60).
  Нерабочие прокси больше не считаются ошибкой запуска.
- **SVM Balance Checker**: 60 кошельков — примерно 8 секунд (раньше ~42).
- **Twitter Checker**: быстрее и стабильнее; при проблеме с прокси запрос
  автоматически повторяется через другой.

### Как обновиться
Если скачивали через `git`:
```bash
git pull
pip install -r requirements.txt
```
Если скачивали архивом — скачайте новый архив и распакуйте поверх старой папки.

---

## English

### Version 1.1.0 — 24.09.2026

#### EVM Balance Checker — only honest balances
- Balances are now **double-checked**: tokens are verified directly on-chain, and the
  total is confirmed by two independent checks through different proxies. No more
  "someone else's" or inflated amounts.
- **Hyperliquid, Lighter and Polymarket** positions come directly from those services.
- New **`unverified`** status (orange) — the balance could not be reliably confirmed.
  Such a wallet is not included in the total. Just check it again.
- New table columns: **Tokens $** (tokens) and **DeFi $** (protocol positions).

#### Collector — more reliable, safer, faster
- **Approve only the swap amount** — no more "unlimited" token approvals.
- **Scam-token protection**: every swap is simulated first. Tokens that can't be sold
  (honeypots, huge fees) are remembered and skipped from then on.
- **Each wallet uses its own proxy** for all requests and transactions. Wallets are not
  linked by IP. Dead proxies are filtered out automatically.
- **One target chain per wallet.** If several target chains are selected, one is picked
  at random for each wallet and all funds are collected there.
- **Smarter bridging**: the route that leaves you the most money is chosen. No bridge is
  made if the fee would eat too much of the amount.
- More accurate gas — transactions don't get stuck and don't overpay.
- If the connection drops, the transaction is safely re-sent through another server —
  no risk of double sending.
- **New: "Amount to exchange (% of balance)".** Send not everything but, for example, a
  random 90–95% from each wallet. Default is 100% (as before).
- **New: "Wallets at once"** (1–20). Speeds up large wallet lists.
  Default is 1 — one by one, as before.
- If funds are already on the target chain, a re-run simply sends them to the exchange.
- Fixed exchange transfers when the subaccount address is written in lowercase.
- Fixed BNB Chain, Polygon and similar networks.
- Clearer results: **Swaps / Bridges** as "successful/attempts", **Swapped $**,
  **Bridged $**, **Refuel $**, **Target** (where funds were collected).
- Green **Success** messages in the log, plus a filter button for them.

#### Export
- **TOTAL** row with totals (EVM, SVM, Collector).
- Collector: new **Operations** sheet — every sent transaction with chain, amount,
  hash and status.
- Fixed Collector CSV and XLSX saving errors.

#### Speed
- **Proxy Checker**: 100 proxies in about 9 seconds (was ~60). Dead proxies no longer
  mark the run as failed.
- **SVM Balance Checker**: 60 wallets in about 8 seconds (was ~42).
- **Twitter Checker**: faster and more stable; on a proxy problem the request is
  automatically retried through another proxy.

### How to update
If you installed with `git`:
```bash
git pull
pip install -r requirements.txt
```
If you downloaded an archive — download the new one and unpack it over the old folder.
