"""
Lead-magnet генератор для Telegram-канала @posleprocedur.

Генерирует через Ollama 7 подробных календарей восстановления — по одному
на ключевую процедуру. Эти посты потом закрепляются в TG и работают как
магнит для подписчиков, приходящих с YouTube (по QR в CTA-карточке).

Запуск standalone:  python3 lead_magnets.py [procedure]
Через бот:          /post_magnets
"""
import json
import logging
import os
import re
import requests
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR    = Path(__file__).parent
MAGNETS_FILE = BASE_DIR / "lead_magnets.json"

from config import OLLAMA_URL, OLLAMA_MODEL as MODEL, OLLAMA_TIMEOUT, atomic_write_json

# 7 ключевых процедур + читаемые названия для подачи зрителю
PROCEDURES = {
    "laser":       "лазерной шлифовки",
    "biorevit":    "биоревитализации",
    "piling":      "химического пилинга",
    "rf":          "RF-лифтинга",
    "dermaroller": "дермароллера / мезороллера",
    "chistka":     "аппаратной чистки лица",
    "fillers":     "контурной пластики (филлеров)",
    "plazma":      "плазмолифтинга (PRP)",
    "photo":       "фотоомоложения (IPL)",
    "smas":        "SMAS-лифтинга",
}


SYSTEM_PROMPT = """Ты косметолог-эксперт, который ведёт Telegram-канал «После Процедур». Пишешь развёрнутый календарь восстановления — это закреплённый пост, который зритель сохраняет и возвращается к нему весь период заживления.

Формат — Telegram HTML (теги <b>, <i>, эмодзи), длина 1800-3200 символов.

СТРУКТУРА:
1. Заголовок с эмодзи и названием процедуры.
2. Короткое intro (1-2 строки): «Сохраняй и подписывайся».
3. 5 блоков по периодам: День 1-2, День 3-5, День 6-10, День 11-14, После 2 недель. Каждый блок:
   - Что происходит с кожей (1-2 короткие строки)
   - ✅ что делать (2-3 пункта)
   - ❌ что нельзя (2-3 пункта)
4. Финальная плашка «🚨 СРОЧНО к врачу если...» — 2-3 тревожных признака.
5. CTA в конце: «Подпишись → @posleprocedur, новые разборы каждый день» — БЕЗ внешних ссылок.

Тон: разговорный, «ты». Конкретно, без воды. Не используй стоп-фразы вроде «индивидуальный подход», «обратитесь к специалисту по любому поводу» — только конкретные правила.

Верни ТОЛЬКО готовый текст поста с HTML-тегами. Никаких заголовков-комментариев, никаких объяснений."""


def _generate_one(procedure_id: str, procedure_name: str) -> str:
    user_msg = (
        f"Сгенерируй детальный календарь восстановления после {procedure_name}. "
        f"Это закреплённый пост в Telegram-канале — должен быть максимально полезным, "
        f"чтобы зритель захотел подписаться и сохранить."
    )
    resp = requests.post(OLLAMA_URL, json={
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        "stream": False,
        "keep_alive": "24h",
        "think": False,
        "options": {"temperature": 0.6, "top_p": 0.9},
    }, timeout=OLLAMA_TIMEOUT)
    resp.raise_for_status()
    text = resp.json()["message"]["content"].strip()
    # Strip wrapping code-block if Ollama wrapped
    text = re.sub(r'^```\w*\s*|\s*```$', '', text).strip()
    return text


def generate_all() -> dict:
    """Generate calendars for all 7 procedures and save to JSON."""
    existing = {}
    if MAGNETS_FILE.exists():
        existing = json.loads(MAGNETS_FILE.read_text(encoding="utf-8"))

    for proc_id, proc_name in PROCEDURES.items():
        if proc_id in existing:
            logger.info("Skipping %s — already generated", proc_id)
            continue
        logger.info("Generating lead magnet for %s...", proc_id)
        existing[proc_id] = {
            "procedure": proc_id,
            "name":      proc_name,
            "text":      _generate_one(proc_id, proc_name),
        }
        # Save incrementally — long generation, don't lose progress on crash
        atomic_write_json(MAGNETS_FILE, existing)
    return existing


def regenerate(procedure_id: str) -> str:
    """Force-regenerate a single magnet (e.g. if owner doesn't like the wording)."""
    if procedure_id not in PROCEDURES:
        raise ValueError(f"Unknown procedure: {procedure_id}")
    data = {}
    if MAGNETS_FILE.exists():
        data = json.loads(MAGNETS_FILE.read_text(encoding="utf-8"))
    proc_name = PROCEDURES[procedure_id]
    text = _generate_one(procedure_id, proc_name)
    data[procedure_id] = {"procedure": procedure_id, "name": proc_name, "text": text}
    atomic_write_json(MAGNETS_FILE, data)
    return text


def load() -> dict:
    if MAGNETS_FILE.exists():
        return json.loads(MAGNETS_FILE.read_text(encoding="utf-8"))
    return {}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1:
        proc = sys.argv[1]
        print(regenerate(proc))
    else:
        result = generate_all()
        print(f"Generated {len(result)} lead magnets, saved to {MAGNETS_FILE}")
