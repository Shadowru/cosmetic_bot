"""Единая точка для разделяемых констант и общих утилит.

Сюда вынесено только то, что реально дублируется между модулями
или закрывает класс багов (atomic write, ротация логов). Не пытаемся
собрать сюда вообще все magic numbers — это не self-сервис конфиг.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import tempfile
from pathlib import Path
from typing import Any

# --- Ollama ---
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")
# Контентные вызовы Ollama могут стоять в очереди — другие проекты на той же
# машине шарят инстанс. На thinking-моделях (qwen3.6:35b) + конкуренция —
# 900s было мало, 1800s даёт запас. Перебить можно через env.
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "1800"))
# Keep-alive ping должен быть быстрым (не блокировать другую работу при
# проблемах). Если ping висит >10 мин — что-то уже сломалось.
OLLAMA_WARMUP_TIMEOUT = int(os.environ.get("OLLAMA_WARMUP_TIMEOUT", "600"))

# --- Smart-skip пороги (shorts_generator.should_skip_slot) ---
MIN_FRESH_COMBOS = 5
MIN_AVG_AVD_PCT = 22.0
AVD_WINDOW = 10

# --- Логирование ---
LOG_FILE = Path(__file__).parent / "bot.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    """Настраивает root-логгер с ротацией. Идемпотентна — повторный вызов не дублирует handlers."""
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        if isinstance(h, logging.handlers.RotatingFileHandler) and \
                getattr(h, "baseFilename", None) == str(LOG_FILE):
            return
    formatter = logging.Formatter(LOG_FORMAT)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.handlers.RotatingFileHandler)
               for h in root.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)


def atomic_write_json(path: str | os.PathLike, data: Any, *, indent: int | None = 2) -> None:
    """Запись JSON через temp+rename. Защищает от corruption при падении посреди write.

    `os.replace()` атомарен в пределах одной FS на POSIX и Windows.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
