from __future__ import annotations
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

_PRIVATE_KEY_RE = re.compile(r"0x[0-9a-fA-F]{64}")
_LONG_TOKEN_RE = re.compile(r"\S{31,}")  # строки >30 символов без пробелов

_LOG_DIR = Path("logs")


def mask_sensitive(text: str) -> str:
    """Маскирует приватные ключи и длинные токены в тексте."""
    text = _PRIVATE_KEY_RE.sub("0x***", text)
    # Длинные токены (не URL и не hex-адреса, уже замаскированные)
    def _mask_token(m: re.Match) -> str:
        s = m.group(0)
        # Пропускаем URL
        if s.startswith("http") or s.startswith("0x***"):
            return s
        return "***"
    text = _LONG_TOKEN_RE.sub(_mask_token, text)
    return text


class _QtSignalHandler(logging.Handler):
    """Маршрутизирует Python logging → Qt-сигнал UI виджета лога."""

    def __init__(self, signal_fn: Any) -> None:
        super().__init__(logging.INFO)
        self._signal_fn = signal_fn
        self.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)-5s %(message)s", datefmt="%H:%M:%S"
        ))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = mask_sensitive(self.format(record))
            self._signal_fn(line)
        except Exception:
            pass


class Logger:
    """Логгер с двумя sink'ами: файл и Qt-сигнал."""

    def __init__(self, on_log_signal: Any = None, name: str = "farmerpro"):
        self._signal = on_log_signal
        self._log_dir = _LOG_DIR
        self._log_dir.mkdir(exist_ok=True)
        log_file = self._log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.log"
        self._file_logger = logging.getLogger(name)
        if not self._file_logger.handlers:
            handler = logging.FileHandler(log_file, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            self._file_logger.addHandler(handler)
            self._file_logger.setLevel(logging.DEBUG)

        # Подключаем Python logging иерархию "app.*" к UI-сигналу
        if self._signal is not None:
            app_logger = logging.getLogger("app")
            # Удаляем старые signal-обработчики чтобы не дублировать при повторном запуске
            app_logger.handlers = [
                h for h in app_logger.handlers if not isinstance(h, _QtSignalHandler)
            ]
            app_logger.addHandler(_QtSignalHandler(self._signal.emit))
            app_logger.setLevel(logging.INFO)

    def _emit(self, level: str, message: str) -> None:
        safe_msg = mask_sensitive(message)
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {level:5s} {safe_msg}"
        # Файл
        level_map = {"INFO": "info", "WARN": "warning", "ERROR": "error"}
        getattr(self._file_logger, level_map.get(level, "info"))(safe_msg)
        # UI сигнал
        if self._signal is not None:
            self._signal.emit(line)

    def info(self, message: str) -> None:
        self._emit("INFO", message)

    def warning(self, message: str) -> None:
        self._emit("WARN", message)

    def error(self, message: str) -> None:
        self._emit("ERROR", message)
