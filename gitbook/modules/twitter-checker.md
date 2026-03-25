# Twitter Checker

Checks Twitter tokens — whether an account is live and what its status is.

**Twitter token** (auth_token) — a code from your browser that proves you are logged in. How to find it:
1. Log into Twitter in your browser
2. Press F12 → **Application** tab → **Cookies** → `twitter.com`
3. Find the `auth_token` row and copy its value

## Requirements

- A file with tokens (one token per line)
- Residential [proxies](https://dashboard.travchisproxies.com/billing/aff.php?aff=1488) (required)

{% hint style="danger" %}
Proxies are required. Without them, Twitter will block your IP after just a few requests. Use residential [proxies](https://dashboard.travchisproxies.com/billing/aff.php?aff=1488) — they look like regular home connections.
{% endhint %}

## How to Run

1. Open the **Twitter Checker** tab
2. Load your tokens file
3. Load your proxy file
4. Click **Start**

## Output Columns

| Column | Description |
|--------|-------------|
| `token` | First characters of the token (rest is hidden) |
| `username` | Account name (@username) |
| `status` | Account status |

### Statuses

| Status | Meaning |
|--------|---------|
| `ok` | Account is live and active |
| `invalid` | Token is invalid or expired |
| `suspended` | Account permanently banned by Twitter |
| `locked` | Account temporarily frozen — needs verification |
| `error` | Request error — usually a proxy issue |
