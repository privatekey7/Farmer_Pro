"""
Простой дисковый кэш JSON в .cache/ корня проекта (между запусками).

Хранит то, что меняется редко или никогда: список сетей Rabby, реестр
chainid.network, адреса Polymarket-proxy (детерминированы CREATE2).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache"
_LOCK = threading.Lock()


def load(name: str, ttl: float | None = None) -> Any:
    """Содержимое .cache/<name> или None (нет файла, устарел, битый)."""
    path = CACHE_DIR / name
    try:
        if ttl is not None and time.time() - path.stat().st_mtime > ttl:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(name: str, data: Any) -> None:
    """Атомарная запись (tmp + replace); ошибки записи игнорируются."""
    path = CACHE_DIR / name
    with _LOCK:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass


class PersistentDict:
    """Словарь, сохраняемый в .cache/<name> при каждом добавлении ключа."""

    def __init__(self, name: str):
        self._name = name
        self._lock = threading.Lock()
        self._data: dict[str, Any] | None = None

    def _ensure(self) -> dict[str, Any]:
        if self._data is None:
            loaded = load(self._name)
            self._data = loaded if isinstance(loaded, dict) else {}
        return self._data

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._ensure().get(key, default)

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._ensure()

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            data = self._ensure()
            data[key] = value
            snapshot = dict(data)
        save(self._name, snapshot)
