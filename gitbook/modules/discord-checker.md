# Discord Checker

Checks Discord tokens — whether they are valid and what information is linked to the account.

## Requirements

- A file with tokens (one token per line)
- Residential [proxies](https://dashboard.travchisproxies.com/billing/aff.php?aff=1488) (required)

{% hint style="danger" %}
Proxies are required. Discord blocks bulk requests from a single IP. Use residential [proxies](https://dashboard.travchisproxies.com/billing/aff.php?aff=1488).
{% endhint %}

## How to Run

1. Open the **Discord Checker** tab
2. Load your tokens file
3. Load your proxy file
4. Click **Start**

## Output Columns

| Column | Description |
|--------|-------------|
| `token` | Start of the token (rest is hidden) |
| `username` | Username |
| `user_id` | Unique account ID |
| `email` | Email address (if linked) |
| `has_phone` | Whether a phone number is linked |
| `status` | Token status |

### Statuses

| Status | Meaning |
|--------|---------|
| `ok` | Token is live, account is active |
| `invalid` | Token is invalid |
| `disabled` | Account disabled by Discord |
| `error` | Request error — usually a proxy issue |
