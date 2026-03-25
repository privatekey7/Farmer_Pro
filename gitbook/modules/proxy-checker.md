# Proxy Checker

Checks a list of proxies — whether they work, how fast they are, and how "clean" they are (i.e., not detectable as proxies).

{% hint style="danger" %}
Proxy Checker only works with **HTTP proxies**. SOCKS5 proxies are not supported.
{% endhint %}

## Requirements

- A file with HTTP proxies

## How to Run

1. Open the **Proxy Checker** tab
2. Load your proxy file
3. Click **Start**

## Output Columns

| Column | Description |
|--------|-------------|
| `proxy` | Proxy address |
| `status` | `OK` — working, `ERROR` — not working |
| `quality` | How "clean" the proxy is |
| `latency_ms` | Response time in milliseconds |

### Quality Levels

| Quality | Meaning |
|---------|---------|
| `high` | Excellent — not detected as a proxy |
| `medium` | Okay — proxy works but is partially detectable |
| `low` | Poor — clearly a datacenter proxy, easily blocked |

{% hint style="info" %}
Twitter and Discord require `high` quality proxies. For EVM balance checks, any working proxy is fine. Buy quality [proxies](https://dashboard.travchisproxies.com/billing/aff.php?aff=1488).
{% endhint %}
