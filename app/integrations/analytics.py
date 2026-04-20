from __future__ import annotations
import platform
import sys
import threading
import uuid
from pathlib import Path

import httpx

POSTHOG_ENDPOINT = "https://app.posthog.com/capture/"
_CLIENT_ID_FILE = Path.home() / ".farmerpro" / "client_id"


def _get_or_create_client_id() -> str:
    try:
        _CLIENT_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _CLIENT_ID_FILE.exists():
            return _CLIENT_ID_FILE.read_text().strip()
        client_id = str(uuid.uuid4())
        _CLIENT_ID_FILE.write_text(client_id)
        return client_id
    except Exception:
        return str(uuid.uuid4())


def _send(api_key: str, event: str, properties: dict) -> None:
    try:
        httpx.post(
            POSTHOG_ENDPOINT,
            json={
                "api_key": api_key,
                "event": event,
                "distinct_id": _get_or_create_client_id(),
                "properties": properties,
            },
            timeout=5.0,
        )
    except Exception:
        pass


def _base_properties() -> dict:
    return {
        "os": platform.system(),
        "os_version": platform.release(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


_API_KEY = "phc_u2SlFlyE4WJaaiIyn4iaU43lSe93MwNvwRFLQRi2gcA"


def track(event: str, properties: dict | None = None) -> None:
    """Отправляет событие в PostHog. Работает в фоновом daemon-потоке."""
    payload = {**_base_properties(), **(properties or {})}
    threading.Thread(target=_send, args=(_API_KEY, event, payload), daemon=True).start()
