import json
import uuid
import os
import random
import datetime
import logging
import requests

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QUEUE_FILE = os.path.join(BASE_DIR, "content_queue.json")
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")

PROCEDURES = ["laser", "biorevit", "piling", "rf", "dermaroller", "chistka",
              "meso", "botox", "fillers", "plazma", "photo", "smas", "general"]
PROCEDURE_NAMES = {
    "laser":       "лазерная шлифовка",
    "biorevit":    "биоревитализация",
    "piling":      "химический пилинг",
    "rf":          "RF-лифтинг",
    "dermaroller": "дермароллер",
    "chistka":     "аппаратная чистка лица",
    "meso":        "мезотерапия",
    "botox":       "ботокс",
    "fillers":     "контурная пластика",
    "plazma":      "плазмолифтинг",
    "photo":       "фотоомоложение",
    "smas":        "SMAS-лифтинг",
    "general":     "уход после процедур",
}

POST_TYPES = ["educational", "myth", "practical", "qa", "light", "engagement",
              "investment", "warning", "universal", "signs", "nobody", "budget"]

POST_TYPE_INSTRUCTIONS = {
    # Процедурно-специфичные
    "educational": "Напиши образовательный пост — объясни что-то важное и конкретное про уход после {proc}. Одна мысль, раскрытая полностью.",
    "myth":        "Напиши пост-развенчание мифа про уход после {proc}. Назови заблуждение, объясни почему оно возникает, скажи что на самом деле надо делать.",
    "practical":   "Напиши практический пост с конкретными действиями по дням или шагам после {proc}. Что делать и чего точно не делать.",
    "qa":          "Напиши пост-ответ на реальный вопрос подписчицы про уход после {proc}. Вопрос должен быть тем, что реально гуглят.",
    "light":       "Напиши лёгкий неформальный пост на тему ухода после {proc}. Без лекций, тон тёплый или с иронией. Выходной формат.",
    "engagement":  "Напиши вовлекающий пост про уход после {proc} — задай конкретный вопрос или сделай мини-опрос с вариантами. В конце скажи когда будет ответ.",
    "signs":       "Напиши пост про признаки нормального заживления после {proc} и про то, когда уже стоит насторожиться. Конкретно: вот что нормально, вот что не нормально.",
    # Общие — без привязки к процедуре (proc игнорируется)
    "investment":  "Напиши пост про то, как неправильный уход после косметологической процедуры сводит результат к нулю. Тон спокойный, но с подтекстом «деньги потрачены зря». Одна конкретная ошибка, что происходит с кожей, как делать правильно. Не называй конкретную процедуру — говори про любую.",
    "warning":     "Напиши пост-предупреждение про ошибку ухода после косметологических процедур, о которой мало кто говорит открыто. Формат: ситуация — что идёт не так с кожей — как правильно. Конкретно, без воды, без названия процедуры.",
    "universal":   "Напиши пост про универсальные правила первых 72 часов после любой косметологической процедуры. Три-четыре правила, которые работают везде. Не перечисляй через точки — раскрой каждое коротким абзацем.",
    "nobody":      "Напиши пост в стиле 'никто не говорит вслух' — инсайд про уход после косметологических процедур, который косметологи знают, но клиентам не объясняют. Одна конкретная вещь, неочевидная для большинства.",
    "budget":      "Напиши пост про то, как правильно ухаживать после косметологической процедуры если бюджет ограничен. Что из профессионального можно заменить аптечным и без потери результата, а что нельзя.",
}

SYSTEM_PROMPT = """Ты — автор Telegram-канала про уход после косметологических процедур. Читательницы — женщины, которые только что сделали процедуру и не знают что делать дальше.

Голос: разговорный, «ты» (не «вы»). Как пишешь подруге, которая разбирается в теме. Конкретно, без воды.

Запрещено: жирные заголовки, списки с галочками или крестиками, слова «итак», «таким образом», «подводя итог», «хочу рассказать». Не начинай пост со слова «сегодня». Эмодзи — максимум одно-два на весь пост.

Формат: короткие абзацы (2-4 строки), между ними пустая строка. 150-250 слов.

Примеры постов из канала:

---
синяк после биоревита — это не значит, что что-то пошло не так

примерно у каждой второй они появляются. просто игла задела сосудик — кровь вышла в ткань. ни врач не виноват, ни ты. просто так устроены сосуды.

что реально помогает убрать быстрее: сразу после процедуры — холод, но не лёд прямо на кожу, а что-то завёрнутое в ткань, минут по пять с перерывами. на следующий день — гель с арникой или гепарином, наносить аккуратно вокруг, без растирания.

дня через три синяк начнёт желтеть. это хороший знак, значит рассасывается как надо. вот тогда уже можно аккуратно перекрыть консилером.
---

«я после пилинга продолжаю мазать ретинол, ничего же страшного?»

страшного, может, и нет, но смысла точно нет — и вот почему.

ретинол ускоряет обновление клеток и усиливает шелушение. пилинг делает то же самое, только сразу и интенсивно. когда ты наносишь ретинол на кожу, которая только что пережила пилинг — ты как будто добавляешь вторую тренировку на следующий день после того, как мышцы уже не сгибаются.

итог — раздражение, долгое шелушение, в худшем случае пигментация.
---

лазерная шлифовка послезавтра — что должно лежать дома к твоему возвращению

потому что после процедуры ты не захочешь никуда ехать и что-то искать. проверено.

нужно: мягкая пенка или гель для умывания без запаха (без SLS), что-то успокаивающее с пантенолом или центеллой, термальная вода в спрее и физический SPF 50 — именно физический, с оксидом цинка или диоксидом титана.
---

Пиши только текст поста. Никаких предисловий и комментариев."""


def load_queue() -> list:
    if os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_queue(items: list) -> None:
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _next_procedure() -> str:
    queue = load_queue()
    recent = [item["procedure"] for item in queue[-6:]]
    unused = [p for p in PROCEDURES if p not in recent]
    return random.choice(unused) if unused else random.choice(PROCEDURES)


def _next_post_type() -> str:
    queue = load_queue()
    recent_types = [item["post_type"] for item in queue[-len(POST_TYPES):] if "post_type" in item]
    unused = [t for t in POST_TYPES if t not in recent_types]
    return unused[0] if unused else POST_TYPES[len(queue) % len(POST_TYPES)]


def generate_post(procedure: str = None, post_type: str = None) -> dict:
    if not procedure:
        procedure = _next_procedure()
    if not post_type:
        post_type = _next_post_type()

    proc_name = PROCEDURE_NAMES.get(procedure, procedure)
    instruction = POST_TYPE_INSTRUCTIONS[post_type].format(proc=proc_name)

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": instruction},
            ],
            "stream": False,
            "keep_alive": "24h",
            "options": {"temperature": 0.8, "top_p": 0.9},
        },
        timeout=900,
    )
    response.raise_for_status()
    text = response.json()["message"]["content"].strip()

    item = {
        "id": str(uuid.uuid4())[:8],
        "type": "post",
        "procedure": procedure,
        "post_type": post_type,
        "text": text,
        "status": "pending",
        "created_at": datetime.datetime.now().isoformat(),
        "review_message_id": None,
    }

    queue = load_queue()
    queue.append(item)
    save_queue(queue)

    logger.info("Сгенерирован пост [%s] %s / %s", item["id"], procedure, post_type)
    return item
