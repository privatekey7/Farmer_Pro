# app/modules/token_collector/_bad_tokens.py
"""Постоянный чёрный список токенов, которые нельзя продать.

Токен попадает сюда, когда симуляция свопа (eth_estimateGas по транзакции из
котировки) откатывается: honeypot, заградительная комиссия на продажу и т.п.
Котировка при этом выглядит нормальной (USA на bsc: $0.40 → $0.37), но реальный
своп откатывается и сжигает газ — а до этого ради него мог быть сделан refuel.
В следующих прогонах такие токены пропускаются без approve, refuel и газа.

Хранится в .cache/collector_bad_tokens.json; удалить файл — сбросить список.
"""
from __future__ import annotations

import time

from app.core.diskcache import PersistentDict

_BAD = PersistentDict("collector_bad_tokens.json")


def _key(chain_id: int, contract: str) -> str:
    return f"{int(chain_id)}:{(contract or '').lower()}"


def is_bad(chain_id: int, contract: str) -> bool:
    return bool(contract) and _key(chain_id, contract) in _BAD


def mark_bad(chain_id: int, contract: str, symbol: str, reason: str) -> None:
    if contract:
        _BAD.set(_key(chain_id, contract), {"symbol": symbol, "reason": reason[:200], "at": int(time.time())})
