from __future__ import annotations
import time
from app.core.models import ProxyConfig
from app.integrations.http_client import build_client

PIXELSCAN_URL = "https://212133867.extension.pixelscan.net/"
# Оригинальный URL содержит фрагмент (#212133867), который HTTP-клиенты
# отбрасывают согласно RFC 3986.
TIMEOUT = 15.0

PIXELSCAN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) Gecko/20100101 Firefox/145.0",
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Connection": "keep-alive",
}


async def check_quality(proxy: ProxyConfig) -> dict:
    """
    GET pixelscan.net через прокси.
    Возвращает {"quality": "high"/"medium"/"low"/"unknown", "latency_ms": int}.
    Бросает исключение при недоступности прокси.
    """
    t0 = time.monotonic()
    async with build_client(proxy, timeout=TIMEOUT) as client:
        resp = await client.get(PIXELSCAN_URL, headers=PIXELSCAN_HEADERS)
        resp.raise_for_status()
        latency_ms = round((time.monotonic() - t0) * 1000)
        try:
            data = resp.json()
            quality = data.get("quality", "unknown")
        except Exception:
            quality = "unknown"
    return {"quality": quality, "latency_ms": latency_ms}
