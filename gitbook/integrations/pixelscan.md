# Pixelscan

Used to assess proxy quality in the **Proxy Checker** module. Each proxy is tested by sending a request through it to `pixelscan.net`, which analyses the connection characteristics and returns a quality score.

## Purpose

| Module | Role |
|--------|------|
| Proxy Checker | Determines `quality` and `latency_ms` for each proxy |

## How it works

```python
result = await check_quality(proxy)
# {"quality": "high" | "medium" | "low" | "unknown", "latency_ms": int}
```

1. Sends a GET request through the proxy to `pixelscan.net` with browser-like headers
2. Measures response time (latency)
3. Parses the `quality` field from the JSON response

## Quality levels

| Quality | Meaning |
|---------|---------|
| `high` | Proxy is undetected, low latency |
| `medium` | Proxy works but is partially detectable |
| `low` | Proxy is clearly identified as a datacenter or proxy |
| `unknown` | Response received but `quality` field is missing |

{% hint style="warning" %}
Pixelscan works with HTTP proxies only. SOCKS5 proxies are not supported and will return an error.
{% endhint %}

{% hint style="warning" %}
If the proxy is unreachable or returns an error, an exception is raised. Proxy Checker catches it and records the status as `ERROR`.
{% endhint %}

## Timeout

Requests are limited to **15 seconds**. Proxies with latency above this value are considered non-working.
