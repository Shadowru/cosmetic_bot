import asyncio
import json
import os
import re
import uuid
import random
import datetime
import logging
import subprocess
import warnings
from pathlib import Path

import requests
import numpy as np
import scipy.io.wavfile as _wavfile
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from generator import load_queue, save_queue

logger = logging.getLogger(__name__)

BASE_DIR    = Path(__file__).parent
SHORTS_DIR  = BASE_DIR / "shorts"
MUSIC_DIR   = BASE_DIR / "music"
SHORTS_DIR.mkdir(exist_ok=True)
MUSIC_DIR.mkdir(exist_ok=True)

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL      = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")

FONT_BOLD   = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_NORMAL = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

W, H   = 1080, 1920
FPS    = 25
TRANS  = 0.35   # audio crossfade / background xfade duration

SAFE_X  = 90
SAFE_Y1 = 240
SAFE_Y2 = H - 300
HANDLE  = "@posleprocedur"

PROC_COLORS = {
    "laser":       {"base": (14, 24, 54),  "a1": (60,  125, 245), "a2": (108, 170, 255)},
    "biorevit":    {"base": (50, 14, 30),  "a1": (235, 85,  132), "a2": (255, 152, 188)},
    "piling":      {"base": (12, 40, 22),  "a1": (50,  200, 98),  "a2": (98,  238, 145)},
    "rf":          {"base": (30, 10, 52),  "a1": (152, 68,  240), "a2": (195, 130, 255)},
    "dermaroller": {"base": (42, 22, 6),   "a1": (230, 152, 48),  "a2": (255, 205, 96)},
    "chistka":     {"base": (8,  36, 46),  "a1": (42,  190, 215), "a2": (85,  232, 250)},
    "general":     {"base": (18, 16, 28),  "a1": (198, 165, 90),  "a2": (232, 210, 140)},
    "meso":        {"base": (8,  38, 32),  "a1": (42,  200, 158), "a2": (85,  240, 200)},
    "botox":       {"base": (22, 22, 38),  "a1": (155, 155, 210), "a2": (205, 205, 245)},
    "fillers":     {"base": (38, 14, 22),  "a1": (220, 110, 150), "a2": (248, 170, 200)},
    "plazma":      {"base": (38, 12, 14),  "a1": (210, 70,  80),  "a2": (240, 130, 140)},  # коралл/плазма
    "photo":       {"base": (38, 32, 6),   "a1": (235, 210, 60),  "a2": (250, 230, 130)},  # золотой свет IPL
    "smas":        {"base": (24, 28, 38),  "a1": (170, 185, 215), "a2": (210, 220, 245)},  # стальной/жемчуг
}

# Thematic pattern per procedure
PROC_PATTERNS = {
    "laser":       "laser",    # diagonal grid + light points
    "biorevit":    "ripples",  # water-drop ripple circles
    "piling":      "layers",   # horizontal skin-layer bands
    "rf":          "rings",    # concentric ellipses (EM waves)
    "dermaroller": "dots",     # micro-needle dot grid
    "chistka":     "bubbles",  # foam bubble clusters
    "general":     "glow",
    "meso":        "ripples",  # injection drops like biorevit
    "botox":       "rings",    # precision waves
    "fillers":     "glow",     # premium soft glow
    "plazma":      "ripples",  # injection drops (PRP, like biorevit but different colour)
    "photo":       "glow",     # IPL light pulses
    "smas":        "rings",    # ultrasound concentric waves
}

# Ken Burns camera movement per segment (zoompan expressions)
SEG_ZOOMS = [
    "z='min(zoom+0.0007,1.14)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",   # zoom in center
    "z='min(zoom+0.0005,1.10)':x='0':y='ih/2-(ih/zoom/2)'",                    # drift right
    "z='min(zoom+0.0006,1.12)':x='iw-iw/zoom':y='0'",                          # drift from top-right
    "z='max(1,1.12-0.0004*on)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",    # zoom out center
    "z='min(zoom+0.0004,1.08)':x='iw/2-(iw/zoom/2)':y='ih-(ih/zoom)'",         # drift up from bottom
]

# Per-card tempo: <1 = slower, >1 = faster (FFmpeg atempo)
CARD_TEMPO = {
    "hook":   0.93,
    "block1": 0.88,
    "block2": 0.88,
    "block3": 0.85,
    "cta":    0.82,
}

SILERO_SPEAKER = "xenia"
SILERO_RATE    = 48000

# Latin abbreviations → Russian pronunciation
_TTS_ABBREVS = [
    (r'\bSPF\s*50\b',  'эс-пэ-эф пятьдесят'),
    (r'\bSPF\s*30\b',  'эс-пэ-эф тридцать'),
    (r'\bSPF\b',       'эс-пэ-эф'),
    (r'\bUVA\b',       'ультрафиолет А'),
    (r'\bUVB\b',       'ультрафиолет Б'),
    (r'\bUV\b',        'ультрафиолет'),
    (r'\bpH\b',        'пэ аш'),
    (r'\bRF\b',        'эр-эф'),
    (r'\bLED\b',       'эль-и-ди'),
    (r'\bBBL\b',       'би-би-эль'),
    (r'\bIPL\b',       'и-пэ-эль'),
    (r'\bAHA\b',       'а-ха кислоты'),
    (r'\bBHA\b',       'бэ-ха кислоты'),
    (r'\bSLS\b',       'эс-эль-эс'),
    (r'\bPRP\b',       'пэ-эр-пэ'),
]

_silero_model = None


def _preprocess_tts(text: str) -> str:
    """Convert digits and Latin abbreviations to pronounceable Russian."""
    from num2words import num2words

    # Abbreviations first (before digit replacement touches SPF50 etc.)
    for pattern, replacement in _TTS_ABBREVS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Digits → Russian words (e.g. "3 дня" → "три дня")
    def _num(m):
        try:
            return num2words(int(m.group()), lang='ru')
        except Exception:
            return m.group()
    text = re.sub(r'\b\d+\b', _num, text)

    return text

TEMPLATES = {
    # Оригинальные
    "mistakes":   "ошибки которые делают все после {proc}",
    "days":       "первые 3 дня после {proc} — что реально нужно делать",
    "forbidden":  "что нельзя делать после {proc}",
    "vs":         "аптечный крем vs профессиональная косметика после {proc}",
    # Новые
    "signs":      "как понять что кожа заживает правильно после {proc}",
    "myths":      "мифы про уход после {proc} которые ты точно слышала",
    "first_time": "делаешь {proc} впервые — вот что тебя ждёт по дням",
    "speed":      "как ускорить заживление после {proc} — три вещи которые реально работают",
    "repeat":     "через сколько снова делать {proc} и почему все называют неправильную цифру",
    "nobody":     "никто не говорит вслух что нельзя делать сразу после {proc}",
    "day_in_life":"день после {proc} — что я реально делаю с утра до вечера",
    # Расширение топ-формата `days` (39.6% AVD на biorevit) — семейство таймлайнов
    "days_7":     "неделя после {proc} — что меняется по дням",
    "day_vs":     "день 1 vs день 7 после {proc} — что реально меняется",
    "recovery_calendar": "календарь восстановления после {proc} — 14 дней по шагам",
    # YT Studio совет: per-procedure «норма vs тревога» — отвечает на «is this OK?» паттерн
    "norm_alarm": "норма или тревога после {proc} — отличаем за 30 секунд",
}

# Templates excluded from auto-selection (low AVD/views in production data)
# Empty for now: first_time показал хорошее retention с новым форматом (kicker+1/3),
# дали шанс ещё раз. Если swipe-to-watch не вырастет за 2 недели — вернуть.
TEMPLATE_BLACKLIST: set[str] = set()

# General topics — не привязаны к конкретной процедуре
GENERAL_TEMPLATES = {
    # Оригинальные
    "money":       "деньги на процедуру уходят впустую из-за неправильного ухода после",
    "sun":         "солнце после косметологической процедуры — главная причина потраченных денег",
    "timing":      "когда возвращаться к обычному уходу после процедур — и почему все ошибаются",
    "retinol":     "ретинол и кислоты после процедур — когда можно и почему все нарушают сроки",
    "universal":   "три правила ухода которые работают после любой косметологической процедуры",
    # Новые
    "reactions":   "нормальная реакция или уже тревога — как понять после косметологической процедуры",
    "ingredients": "эти ингредиенты разрушат результат любой процедуры — проверь свой крем прямо сейчас",
    "budget":      "профессиональный уход после процедур если бюджет ограничен",
}

PROCEDURES = ["laser", "biorevit", "piling", "rf", "dermaroller", "chistka",
              "meso", "botox", "fillers", "plazma", "photo", "smas", "general"]
PROC_NAMES = {
    "laser":      "лазерной шлифовки",
    "biorevit":   "биоревитализации",
    "piling":     "пилинга",
    "rf":         "RF-лифтинга",
    "dermaroller":"дермароллера",
    "chistka":    "чистки лица",
    "meso":       "мезотерапии",
    "botox":      "ботокса",
    "fillers":    "контурной пластики",
    "plazma":     "плазмолифтинга",
    "photo":      "фотоомоложения",
    "smas":       "SMAS-лифтинга",
    "general":    "косметологических процедур",
}
PROC_NAMES_NOM = {
    "laser":      "лазерная шлифовка",
    "biorevit":   "биоревитализация",
    "piling":     "химический пилинг",
    "rf":         "RF-лифтинг",
    "dermaroller":"дермароллер",
    "chistka":    "аппаратная чистка лица",
    "meso":       "мезотерапия",
    "botox":      "ботокс",
    "fillers":    "контурная пластика",
    "plazma":     "плазмолифтинг",
    "photo":      "фотоомоложение",
    "smas":       "SMAS-лифтинг",
    "general":    "уход после процедур",
}

GENERAL_SCRIPT_SYSTEM = """Ты пишешь сценарии для 30-секундных Shorts про то, почему деньги на косметологические процедуры уходят впустую. Аудитория — женщины, которые делают процедуры.

Голос: разговорный, «ты», честный. Говоришь как подруга, которая видела как люди теряют результат из-за ошибок.

ВАЖНО — структура удержания:
1. Хук — curiosity_gap + явное обещание "3 [ошибки/причины/правила]" без раскрытия ответа. Незакрытый список в голове зрителя удерживает до конца.
   Примеры: «3 причины почему дорогой уход не даёт результата — и ни одна из них не в составе»
            «Косметологи знают 3 вещи которые клиенты делают неправильно — и молчат об этом»
            «3 ошибки в первые дни после процедуры которые сводят весь эффект к нулю»
2. block1/2/3 — три чётких пункта. На экране будет метка 1/3, 2/3, 3/3.
3. block3 — punchline: самый сильный/неочевидный пункт, заканчивается коротким вопросом к зрителю (например «а у тебя так было?», «а ты замечала?»). Вопрос на пике удержания провоцирует комментарии.
4. CTA — НЕ задавай вопрос, не «подписывайся». Только короткая фраза-передача ценности: «Полный список — в Telegram @posleprocedur» или «Забирай схему по дням в Telegram @posleprocedur».

Верни ТОЛЬКО валидный JSON без пояснений:
{
  "hook":   "1 предложение — curiosity_gap + обещание '3 [чего-то]' без раскрытия",
  "block1": "1 предложение — первый пункт (коротко, конкретно)",
  "block2": "1 предложение — второй пункт (коротко, конкретно)",
  "block3": "1 предложение — самый сильный пункт + короткий вопрос в конце",
  "cta":    "1 фраза — короткая передача КОНКРЕТНОГО магнита с ссылкой. Не 'полный список', а 'календарь по дням'. Пример: 'Подробный календарь по дням после {proc} — в закрепе Telegram @posleprocedur'"
}

Суммарно 45-60 слов — ровно 30 секунд речи. Никакого текста кроме JSON."""

SCRIPT_SYSTEM = """Ты пишешь сценарии для 30-секундных Shorts про уход после косметологических процедур. Аудитория — женщины только что после процедуры.

Голос: разговорный, «ты», без клише. Конкретно и по делу, как подруга-косметолог.

ВАЖНО — структура удержания:
1. Хук — curiosity_gap + явное обещание "3 [правила/признака/ошибки/шага]" без раскрытия ответа. Это создаёт незакрытый список в голове зрителя и заставляет досмотреть все три пункта.
   Примеры: «Есть 3 вещи которые делают все после {proc} — и именно они разрушают результат»
            «3 признака после {proc} которые кажутся нормой — но это не так»
            «Косметологи знают 3 правила после {proc} о которых клиентам не говорят»
2. block1/2/3 — три чётких коротких пункта, каждый самодостаточен. На экране будет метка 1/3, 2/3, 3/3 — зритель видит прогресс и ждёт следующий.
3. block3 — punchline: самый сильный/неочевидный пункт, заканчивается коротким вопросом к зрителю (например «а у тебя так было?», «а ты замечала?», «знала об этом?»). Вопрос на пике удержания провоцирует ответы в комментариях.
4. CTA — НЕ задавай вопрос (уже задан в block3), не «подписывайся». Только короткая фраза-передача ценности: «Полный список — в Telegram @posleprocedur» или «Забирай схему по дням в Telegram @posleprocedur». Ритм не должен провисать.

Верни ТОЛЬКО валидный JSON без пояснений:
{
  "hook":   "1 предложение — curiosity_gap + обещание '3 [чего-то]' без раскрытия",
  "block1": "1 предложение — первый пункт (коротко, конкретно)",
  "block2": "1 предложение — второй пункт (коротко, конкретно)",
  "block3": "1 предложение — самый сильный пункт + короткий вопрос в конце",
  "cta":    "1 фраза — короткая передача КОНКРЕТНОГО магнита с ссылкой. Не 'полный список', а 'календарь по дням'. Пример: 'Подробный календарь по дням после {proc} — в закрепе Telegram @posleprocedur'"
}

Суммарно 45-60 слов — ровно 30 секунд речи. Никакого текста кроме JSON."""

DAY_IN_LIFE_SCRIPT_SYSTEM = """Ты пишешь сценарий для 30-секундного Shorts в формате «День в жизни после процедуры». Аудитория — женщины которые только что сделали процедуру.

Голос: разговорный, от первого лица «я», конкретно. Никаких абстрактных советов — только что и когда я делаю.

ФОРМАТ:
- Хук — обещание показать день целиком (НЕ curiosity_gap). Зритель хочет посмотреть «реальный день» как мини-влог.
  Примеры: «День 3 после {proc} — показываю что я реально делаю с утра до вечера»
           «Один день после {proc}: 3 действия которые сохраняют результат»
           «Мой день после {proc} — почему именно так, а не как все советуют»
- block1/2/3 — три действия с привязкой ко времени. Каждый начинается со времени или периода дня (9 утра / в обед / вечер / 21:00).
  Примеры: «9 утра — умываюсь прохладной водой без средств»
           «В обед — пантенол тонким слоем, маски пока нельзя»
           «Вечер — никакой косметики, только увлажнение и сон до 23»

Верни ТОЛЬКО валидный JSON без пояснений:
{
  "hook":   "1 предложение — обещание показать день после {proc}",
  "block1": "1 предложение — утреннее действие со временем",
  "block2": "1 предложение — дневное/обеденное действие со временем",
  "block3": "1 предложение — вечернее действие со временем + короткий вопрос (а ты что делаешь вечером?)",
  "cta":    "1 фраза — короткая передача конкретного магнита. Пример: 'Подробный календарь дня по дням — в закрепе Telegram @posleprocedur'"
}

Суммарно 45-60 слов. Никакого текста кроме JSON."""


HOOK_STYLE = {
    "mistakes":   "Hook (curiosity_gap): намекни что есть ошибка которую делают все после {proc} — но не называй её. Пример: «Все делают одно и то же после {proc} — и именно это разрушает результат».",
    "forbidden":  "Hook (curiosity_gap): создай интригу запрета без объяснения причины. Пример: «Косметолог запретила мне делать X после {proc} — и только потом я поняла почему».",
    "days":       "Hook (curiosity_gap): намекни что первые дни после {proc} скрывают что-то неочевидное. Пример: «Все думают что знают что делать в первые дни после {proc} — но вот что реально важно».",
    "vs":         "Hook (curiosity_gap): создай неочевидный выбор — ответ будет неожиданным. Пример: «Аптечный крем или профессиональный после {proc} — ответ косметологов удивил даже меня».",
    "signs":      "Hook (curiosity_gap): зритель не знает отличить норму от тревоги — зацепи этой неопределённостью. Пример: «Есть один признак после {proc} который выглядит нормально — но это не так».",
    "myths":      "Hook (curiosity_gap): назови миф косвенно, не раскрывая его. Пример: «То что все советуют делать после {proc} — на самом деле мешает заживлению».",
    "first_time": "Hook (curiosity_gap): намекни что первый раз открывает что-то что обычно скрывают. Пример: «Делаешь {proc} впервые? Есть кое-что что тебе не скажут в кабинете».",
    "speed":      "Hook (curiosity_gap): намекни что способ ускорить заживление неочевиден. Пример: «Кожа после {proc} восстанавливается медленно — пока не знаешь вот эту одну вещь».",
    "repeat":     "Hook (curiosity_gap): создай разрыв через неправильное общепринятое мнение о сроках. Пример: «Все называют разные сроки когда повторять {proc} — и почти все называют неправильно».",
    "nobody":     "Hook (curiosity_gap): ощущение что зритель сейчас узнает то что скрывают. Пример: «Есть одна вещь про {proc} которую косметологи знают, но никогда не говорят первыми».",
    "day_in_life":"Hook: обещание показать день целиком. Пример: «День 3 после {proc} — показываю что я реально делаю по часам».",
    # Timeline family (расширение топ-формата `days`)
    "days_7":     "Hook: обещание понедельного календаря. Пример: «Неделя после {proc} — показываю что меняется день за днём, и почему именно так». Блоки структурируй как 'День 1-2', 'День 3-4', 'День 5-7'.",
    "day_vs":     "Hook: контраст до/после. Пример: «День 1 vs День 7 после {proc} — разница которую почти никто не видит». Блоки: 'День 1', 'День 3', 'День 7'.",
    "recovery_calendar": "Hook: обещание систематического плана. Пример: «Календарь восстановления после {proc} — 14 дней по шагам, без воды». Блоки: 'Дни 1-3 (острая фаза)', 'Дни 4-7 (стабилизация)', 'Дни 8-14 (восстановление)'.",
    "norm_alarm": "Hook (warning + reassurance): «Есть 3 признака после {proc} — 2 это норма, 1 значит звонить врачу. Покажу как отличить за 30 секунд». СТРУКТУРА БЛОКОВ ЖЁСТКАЯ: block1 — признак-норма с пояснением сроков; block2 — ещё признак-норма (другой тип); block3 — ТРЕВОЖНЫЙ признак (звонить врачу) + микро-вопрос («у тебя такое было?»). НЕ путать порядок. CTA — без вопроса, ценность.",
}

# Hook hints for general (procedure-independent) templates — more visceral/warning tone
GENERAL_HOOK_STYLE = {
    "money":       "Hook (warning): начни с потери денег. Пример: «3 ошибки после процедуры из-за которых деньги уходят впустую — даже у тех кто платил дорого».",
    "sun":         "Hook (warning): солнце как угроза. Пример: «Один час на солнце после процедуры — и результат можно выбрасывать. 3 правила чтобы этого не случилось».",
    "timing":      "Hook (warning): создай страх ошибки со сроками. Пример: «Большинство возвращается к обычному уходу слишком рано — и теряет половину результата процедуры».",
    "retinol":     "Hook (warning): запрет с интригой. Пример: «Если ты используешь ретинол после процедуры — есть 3 вещи о которых нужно знать ДО».",
    "universal":   "Hook: обещание универсальных правил. Пример: «3 правила ухода после любой процедуры — работают всегда, но почти никто не использует все три».",
    "reactions":   "Hook (warning): страх неизвестности. Пример: «3 признака после процедуры которые ты примешь за норму — но это не так».",
    "ingredients": "Hook (warning): угроза в кремах. Пример: «3 ингредиента из обычного крема разрушат любую процедуру — проверь свой прямо сейчас».",
    "budget":      "Hook: неочевидная экономия. Пример: «3 продукта для ухода после процедуры за разумный бюджет — а не за 15 тысяч».",
}

# Visual "kicker" — big word that appears at frame 0 on hook card to grab attention before text loads
KICKER = {
    "mistakes":    "СТОП.",
    "forbidden":   "НЕЛЬЗЯ.",
    "days":        "ВАЖНО.",
    "vs":          "ВЫБИРАЙ.",
    "signs":       "ТРЕВОГА?",
    "myths":       "МИФ.",
    "first_time":  "ПЕРВЫЙ РАЗ?",
    "speed":       "СЕКРЕТ.",
    "repeat":      "ОШИБКА.",
    "nobody":      "ПРАВДА.",
    "day_in_life": "",  # no kicker — narrative format
    "days_7":      "НЕДЕЛЯ.",
    "day_vs":      "ДЕНЬ 1 vs 7.",
    "recovery_calendar": "14 ДНЕЙ.",
    "norm_alarm":  "НОРМА?",
    # general
    "money":       "ВПУСТУЮ.",
    "sun":         "СОЛНЦЕ.",
    "timing":      "ОШИБКА.",
    "retinol":     "СТОП!",
    "universal":   "3 ПРАВИЛА.",
    "reactions":   "ТРЕВОГА?",
    "ingredients": "ПРОВЕРЬ.",
    "budget":      "БЮДЖЕТ.",
}

# 3-part mini-series per procedure: (procedure, template)
SERIES: dict[str, list[tuple[str, str]]] = {
    "biorevit":    [("biorevit",    "days"),  ("biorevit",    "norm_alarm"),("biorevit",    "repeat")],
    "laser":       [("laser",       "days"),  ("laser",       "forbidden"), ("laser",       "norm_alarm")],
    "piling":      [("piling",      "days"),  ("piling",      "norm_alarm"),("piling",      "vs")],
    "rf":          [("rf",          "days"),  ("rf",          "mistakes"),  ("rf",          "norm_alarm")],
    "dermaroller": [("dermaroller", "days"),  ("dermaroller", "forbidden"), ("dermaroller", "norm_alarm")],
    "chistka":     [("chistka",     "days"),  ("chistka",     "mistakes"),  ("chistka",     "norm_alarm")],
    "meso":        [("meso",        "days"),  ("meso",        "norm_alarm"),("meso",        "repeat")],
    "botox":       [("botox",       "days"),  ("botox",       "forbidden"), ("botox",       "norm_alarm")],
    "fillers":     [("fillers",     "days"),  ("fillers",     "forbidden"), ("fillers",     "norm_alarm")],
    "plazma":      [("plazma",      "days"),  ("plazma",      "norm_alarm"),("plazma",      "repeat")],
    "photo":       [("photo",       "days"),  ("photo",       "forbidden"), ("photo",       "norm_alarm")],
    "smas":        [("smas",        "days"),  ("smas",        "forbidden"), ("smas",        "norm_alarm")],
}


def _series_position(procedure: str, template: str) -> tuple[int, int] | None:
    """If (procedure, template) belongs to a SERIES, return (index_1based, total)."""
    for series_proc, items in SERIES.items():
        for i, (p, t) in enumerate(items):
            if p == procedure and t == template:
                return (i + 1, len(items))
    return None


_REQUIRED_SCRIPT_KEYS = ("hook", "block1", "block2", "block3", "cta")


def _call_ollama(system: str, user_msg: str) -> dict:
    """Single Ollama call → parsed JSON script. Retries up to 3 times if any required
    key is missing (Ollama иногда сливает поля или дропает cta)."""
    last_err = None
    for attempt in range(3):
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user_msg},
                ],
                "stream": False,
                "keep_alive": "24h",
                "options": {"temperature": 0.75, "top_p": 0.9},
            },
            timeout=900,
        )
        resp.raise_for_status()
        raw = resp.json()["message"]["content"]
        match = re.search(r'\{[\s\S]+\}', raw)
        if not match:
            last_err = f"no JSON in response: {raw[:200]}"
            logger.warning("Ollama returned no JSON (attempt %d/3)", attempt + 1)
            continue
        try:
            script = json.loads(match.group())
        except json.JSONDecodeError as e:
            last_err = f"invalid JSON: {e}"
            logger.warning("Ollama JSON decode failed (attempt %d/3): %s", attempt + 1, e)
            continue
        missing = [k for k in _REQUIRED_SCRIPT_KEYS if not script.get(k)]
        if missing:
            last_err = f"missing/empty keys: {missing} | got: {list(script.keys())}"
            logger.warning("Ollama missing keys (attempt %d/3): %s", attempt + 1, missing)
            continue
        return script
    raise ValueError(f"Ollama не вернул валидный JSON за 3 попытки: {last_err}")


def _generate_script(procedure: str, template: str) -> dict:
    if procedure == "general":
        topic     = GENERAL_TEMPLATES[template]
        hook_hint = GENERAL_HOOK_STYLE.get(template, "")
        user_msg  = f"Тема ролика: «{topic}»"
        if hook_hint:
            user_msg += f"\n{hook_hint}"
        system    = GENERAL_SCRIPT_SYSTEM
        proc_nom  = "процедуры"
    else:
        proc_gen  = PROC_NAMES.get(procedure, procedure)
        proc_nom  = PROC_NAMES_NOM.get(procedure, procedure)
        topic     = TEMPLATES[template].format(proc=proc_gen)
        hook_hint = HOOK_STYLE.get(template, "")
        if hook_hint:
            hook_hint = hook_hint.replace("{proc}", proc_nom)
        user_msg  = f"Тема ролика: «{topic}»\nПроцедура: {proc_nom}"
        if hook_hint:
            user_msg += f"\n{hook_hint}"
        if template == "day_in_life":
            system = DAY_IN_LIFE_SCRIPT_SYSTEM.replace("{proc}", proc_nom)
        else:
            system = SCRIPT_SYSTEM

    script = _call_ollama(system, user_msg)

    # Высокорисковый шаблон norm_alarm: медицинский fact-check через Gemini.
    # Если Gemini забраковал — регенерируем до 2 раз. Если ключа нет — пропускается.
    if template == "norm_alarm" and procedure != "general":
        from validators import validate_norm_alarm
        for attempt in range(3):
            passed, msg = validate_norm_alarm(script, proc_nom)
            if passed:
                break
            logger.warning("norm_alarm fact-check failed (attempt %d/3): %s — regenerating",
                           attempt + 1, msg)
            script = _call_ollama(system, user_msg)
        else:
            raise ValueError(f"norm_alarm fact-check failed 3x: {msg}")

    return script


# ── Voice ────────────────────────────────────────────────────────────────────

def _get_silero() -> object:
    global _silero_model
    if _silero_model is None:
        import torch
        warnings.filterwarnings("ignore")
        logger.info("Загружаю Silero TTS (первый запуск)...")
        _silero_model, _ = torch.hub.load(
            repo_or_dir="snakers4/silero-models",
            model="silero_tts",
            language="ru",
            speaker="v3_1_ru",
            trust_repo=True,
            verbose=False,
        )
        logger.info("Silero TTS загружена")
    return _silero_model


def _synth_sentences(model, text: str) -> np.ndarray:
    """Synthesize sentence-by-sentence to avoid Silero's internal chunking artifacts."""
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s.strip()]
    if not sentences:
        sentences = [text.strip()]

    # Silero падает с bare ValueError если после внутренней очистки строка пустая
    # (только пунктуация/символы/эмодзи). Фильтруем кандидаты на «без букв».
    sentences = [s for s in sentences if re.search(r'[A-Za-zА-Яа-яЁё]', s)]
    if not sentences:
        logger.warning("TTS skipped: no speakable sentences in text=%r", text[:120])
        return np.zeros(SILERO_RATE // 4, dtype=np.float32)

    pause_short = np.zeros(int(SILERO_RATE * 0.12), dtype=np.float32)
    chunks = []
    for i, sentence in enumerate(sentences):
        try:
            audio = model.apply_tts(
                text=sentence,
                speaker=SILERO_SPEAKER,
                sample_rate=SILERO_RATE,
                put_accent=True,
                put_yo=True,
            )
        except (ValueError, Exception) as e:
            logger.warning("Silero apply_tts failed on sentence %r: %s — skipping", sentence[:80], e)
            continue
        chunks.append(audio.numpy())
        if i < len(sentences) - 1:
            chunks.append(pause_short)

    return np.concatenate(chunks) if chunks else np.zeros(SILERO_RATE // 4, dtype=np.float32)


def _generate_voiceover(text: str, output_path: Path, card_type: str = "block1") -> None:
    model = _get_silero()
    tempo = max(0.5, min(2.0, CARD_TEMPO.get(card_type, 0.88)))

    clean = _preprocess_tts(text)
    arr_f = _synth_sentences(model, clean)
    arr16 = (arr_f * 32767).astype(np.int16)

    wav_tmp = output_path.with_suffix(".wav")
    _wavfile.write(str(wav_tmp), SILERO_RATE, arr16)

    subprocess.run([
        "ffmpeg", "-y", "-i", str(wav_tmp),
        "-filter:a", f"atempo={tempo:.3f}",
        "-c:a", "mp3", "-b:a", "192k",
        str(output_path),
    ], check=True, capture_output=True)
    wav_tmp.unlink(missing_ok=True)


def _get_audio_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(result.stdout)["streams"][0]["duration"])


def _mix_music(voice: Path, output: Path) -> None:
    """Mix a random background track at 18% volume under the voice. Skip if no tracks."""
    tracks = list(MUSIC_DIR.glob("*.mp3")) + list(MUSIC_DIR.glob("*.wav")) + list(MUSIC_DIR.glob("*.m4a"))
    if not tracks:
        import shutil
        shutil.copy(str(voice), str(output))
        return
    track = random.choice(tracks)
    dur   = _get_audio_duration(voice)
    fade_out_start = max(0.0, dur - 2.0)
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(voice),
        "-stream_loop", "-1", "-i", str(track),
        "-filter_complex",
        (
            f"[1:a]volume=0.18,atrim=0:{dur:.3f},"
            f"afade=t=in:d=1,afade=t=out:st={fade_out_start:.3f}:d=2[bg];"
            "[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        ),
        "-map", "[aout]",
        "-c:a", "aac", "-b:a", "192k",
        str(output),
    ], check=True, capture_output=True)
    logger.info("Music mixed: %s", track.name)


# ── Pillow: transparent overlay PNG ─────────────────────────────────────────

def _add_bokeh_rgba(img: Image.Image, procedure: str, seed: int) -> Image.Image:
    colors  = PROC_COLORS.get(procedure, PROC_COLORS["laser"])
    palette = [colors["a1"], colors["a2"]]
    rng     = random.Random(seed)
    hw, hh  = W // 2, H // 2

    layer = Image.new("RGBA", (hw, hh), (0, 0, 0, 0))
    draw  = ImageDraw.Draw(layer)
    for _ in range(3):
        cx = rng.randint(-hw // 2, hw + hw // 2)
        cy = rng.randint(-hh // 3, hh + hh // 3)
        r  = rng.randint(220, 320)
        draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=(*rng.choice(palette), rng.randint(55, 85)))
    for _ in range(14):
        cx = rng.randint(-hw // 3, hw + hw // 3)
        cy = rng.randint(-hh // 5, hh + hh // 5)
        r  = rng.randint(50, 150)
        draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=(*rng.choice(palette), rng.randint(95, 155)))
    layer = layer.filter(ImageFilter.GaussianBlur(radius=36))
    layer = layer.resize((W, H), Image.LANCZOS)
    return Image.alpha_composite(img, layer)


def _add_geometry(img: Image.Image, card_type: str, procedure: str) -> Image.Image:
    colors = PROC_COLORS.get(procedure, PROC_COLORS["laser"])
    a1, a2 = colors["a1"], colors["a2"]
    geo  = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(geo)
    if card_type == "hook":
        r = 620; cx, cy = W - 60, 60
        draw.arc([cx-r, cy-r, cx+r, cy+r], start=90, end=180, fill=(*a1, 190), width=14)
        draw.arc([cx-r+60, cy-r+60, cx+r-60, cy+r-60], start=90, end=180, fill=(*a2, 80), width=6)
    elif card_type in ("block1", "block2", "block3"):
        for i, (alpha, width) in enumerate([(170, 7), (100, 4), (55, 3)]):
            off = i * 60
            draw.line([(-80+off, H-240), (340+off, H+60)], fill=(*a1, alpha), width=width)
    elif card_type == "cta":
        bar_w = 180; bar_x = (W - bar_w) // 2
        draw.rectangle([bar_x, H-270, bar_x+bar_w, H-263], fill=(*a1, 220))
        draw.rectangle([bar_x+40, H-255, bar_x+bar_w-40, H-251], fill=(*a2, 120))
    return Image.alpha_composite(img.convert("RGBA"), geo)


def _font(size: int, bold: bool = True) -> ImageFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_NORMAL, size)


def _wrap(draw: ImageDraw, text: str, font: ImageFont, max_w: int) -> list[str]:
    words = text.split()
    lines, cur = [], []
    for word in words:
        test = " ".join(cur + [word])
        if draw.textlength(test, font=font) <= max_w:
            cur.append(word)
        else:
            if cur:
                lines.append(" ".join(cur))
            cur = [word]
    if cur:
        lines.append(" ".join(cur))
    return lines


def _render_overlay(text: str, card_type: str, procedure: str, frame_idx: int, output_path: Path, template: str = "") -> None:
    """Transparent RGBA PNG: bokeh + geometry + text. Composited over gradient in FFmpeg."""
    colors = PROC_COLORS.get(procedure, PROC_COLORS["laser"])
    accent = colors["a1"]

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img = _add_bokeh_rgba(img, procedure, seed=frame_idx * 137 + abs(hash(procedure)) % 999)
    img = _add_geometry(img, card_type, procedure)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 6, H], fill=(*accent, 220))

    max_w = W - 2 * SAFE_X

    if card_type == "hook":
        # Series badge — top-left, signals «подписывайся за продолжением»
        series_pos = _series_position(procedure, template)
        if series_pos:
            idx, total = series_pos
            sb_text = f"СЕРИЯ · {idx}/{total}"
            sb_font = _font(34, bold=True)
            sb_w    = int(draw.textlength(sb_text, font=sb_font)) + 28
            sb_h    = 54
            sb_x    = SAFE_X
            sb_y    = 60
            draw.rounded_rectangle([sb_x, sb_y, sb_x + sb_w, sb_y + sb_h], radius=12,
                                    fill=(*accent, 220))
            draw.text((sb_x + 14, sb_y + 8), sb_text, font=sb_font, fill=(255, 255, 255, 255))

        # Kicker — bold attention-grabber at frame 0 (above hook text)
        kicker = KICKER.get(template, "")
        kicker_offset = 0
        if kicker:
            k_font = _font(120, bold=True)
            k_w    = draw.textlength(kicker, font=k_font)
            k_h    = draw.textbbox((0, 0), "Ag", font=k_font)[3]
            k_x    = (W - k_w) / 2
            k_y    = 170
            # Tight contrast slab behind kicker
            pad_x, pad_y = 36, 12
            draw.rectangle([k_x - pad_x, k_y - pad_y, k_x + k_w + pad_x, k_y + k_h + pad_y],
                           fill=(0, 0, 0, 220))
            # Bright kicker text (lighter accent variant)
            kicker_col = (min(255, accent[0]+120), min(255, accent[1]+120), min(255, accent[2]+120), 255)
            draw.text((k_x + 4, k_y + 4), kicker, font=k_font, fill=(0, 0, 0, 180))
            draw.text((k_x, k_y), kicker, font=k_font, fill=kicker_col)
            kicker_offset = 160  # push hook text down to leave room

        # Hook text (large, in accent color)
        font  = _font(80)
        color = (min(255, accent[0]+80), min(255, accent[1]+80), min(255, accent[2]+80), 255)
        lines  = _wrap(draw, text, font, max_w)
        line_h = draw.textbbox((0, 0), "Ag", font=font)[3] + 18
        block_h = len(lines) * line_h
        y = SAFE_Y1 + 80 + kicker_offset
        draw.rectangle([0, y - 44, W, y + block_h + 44], fill=(0, 0, 0, 148))
        draw.rectangle([0, y - 44, W, y - 36], fill=(*accent, 230))
        for line in lines:
            x = (W - draw.textlength(line, font=font)) / 2
            draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0, 110))
            draw.text((x, y), line, font=font, fill=color)
            y += line_h

    elif card_type == "cta":
        # «ЗАБИРАЙ →» — value-framed header (не «прощание», а передача ценности)
        h_font = _font(110, bold=True)
        h_text = "ЗАБИРАЙ →"
        h_w    = draw.textlength(h_text, font=h_font)
        h_h    = draw.textbbox((0, 0), "Ag", font=h_font)[3]
        h_x    = (W - h_w) / 2
        h_y    = 200
        pad_x, pad_y = 30, 10
        draw.rectangle([h_x - pad_x, h_y - pad_y, h_x + h_w + pad_x, h_y + h_h + pad_y],
                       fill=(0, 0, 0, 220))
        accent_col = (min(255, accent[0]+120), min(255, accent[1]+120), min(255, accent[2]+120), 255)
        draw.text((h_x + 3, h_y + 3), h_text, font=h_font, fill=(0, 0, 0, 180))
        draw.text((h_x, h_y), h_text, font=h_font, fill=accent_col)

        # Подзаголовок-ценность (текст из script.cta — обычно «полный список средств — в Telegram»)
        font  = _font(48, bold=False)
        color = (230, 230, 230, 255)
        lines  = _wrap(draw, text, font, max_w)
        line_h = draw.textbbox((0, 0), "Ag", font=font)[3] + 12
        block_h = len(lines) * line_h
        y = h_y + h_h + 80
        for line in lines:
            x = (W - draw.textlength(line, font=font)) / 2
            draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0, 140))
            draw.text((x, y), line, font=font, fill=color)
            y += line_h

    else:
        # block1/2/3 — full-width caption strip at bottom third
        font  = _font(66)
        color = (255, 255, 255, 255)
        lines  = _wrap(draw, text, font, max_w)
        line_h = draw.textbbox((0, 0), "Ag", font=font)[3] + 16
        block_h = len(lines) * line_h
        # Position: bottom third of safe zone
        y = SAFE_Y2 - block_h - 120
        # Full-width dark strip
        draw.rectangle([0, y - 36, W, y + block_h + 36], fill=(0, 0, 0, 185))
        draw.rectangle([0, y - 36, W, y - 26], fill=(*accent, 220))
        for line in lines:
            x = (W - draw.textlength(line, font=font)) / 2
            draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0, 120))
            draw.text((x, y), line, font=font, fill=color)
            y += line_h
        # Progress badge: "1/3", "2/3", "3/3" — top-right corner
        badge_num = {"block1": "1", "block2": "2", "block3": "3"}.get(card_type, "")
        if badge_num:
            b_font = _font(42, bold=True)
            b_text = f"{badge_num}/3"
            b_w = int(draw.textlength(b_text, font=b_font)) + 28
            b_h = 62
            b_x = W - SAFE_X - b_w
            b_y = SAFE_Y1 + 20
            draw.rounded_rectangle([b_x, b_y, b_x + b_w, b_y + b_h], radius=14,
                                    fill=(*accent, 200))
            draw.text((b_x + 14, b_y + 10), b_text, font=b_font, fill=(255, 255, 255, 255))

    if card_type == "cta":
        # QR + Telegram + screenshot prompt (по рекомендации YouTube — конвертирует досмотревших)
        import qrcode as _qrcode
        qr = _qrcode.QRCode(version=2, error_correction=_qrcode.constants.ERROR_CORRECT_L,
                            box_size=5, border=2)
        qr.add_data("https://t.me/posleprocedur")
        qr.make(fit=True)
        qr_pil = qr.make_image(fill_color=(10, 10, 10), back_color=(255, 255, 255)).convert("RGBA")
        qr_size = 172
        qr_pil = qr_pil.resize((qr_size, qr_size), Image.LANCZOS)
        qr_x, qr_y = SAFE_X, H - 295
        draw.rectangle([qr_x - 8, qr_y - 8, qr_x + qr_size + 8, qr_y + qr_size + 8],
                       fill=(255, 255, 255, 255))
        img.paste(qr_pil, (qr_x, qr_y))
        # Telegram link справа от QR
        tx = qr_x + qr_size + 26
        draw.text((tx, qr_y + 18),  "Календарь по дням →", font=_font(32, bold=False), fill=(200, 200, 200, 230))
        draw.text((tx, qr_y + 64),  "t.me/",              font=_font(38, bold=False), fill=(180, 180, 180, 200))
        draw.text((tx, qr_y + 110), "posleprocedur",      font=_font(38, bold=True),  fill=(*accent, 255))
        # «📸 СОХРАНИ» — прямая инструкция, конвертирует досмотревших в подписчиков TG
        s_text = "📸  СОХРАНИ"
        s_font = _font(40, bold=True)
        s_w    = int(draw.textlength(s_text, font=s_font)) + 32
        s_h    = 58
        s_x    = (W - s_w) // 2
        s_y    = qr_y + qr_size + 20
        draw.rounded_rectangle([s_x, s_y, s_x + s_w, s_y + s_h], radius=12,
                                fill=(*accent, 220))
        draw.text((s_x + 16, s_y + 6), s_text, font=s_font, fill=(255, 255, 255, 255))
    else:
        h_font = _font(38, bold=False)
        h_w = draw.textlength(HANDLE, font=h_font)
        draw.text(((W - h_w) / 2, H - 190), HANDLE, font=h_font, fill=(180, 180, 180, 200))
    img.save(str(output_path), "PNG")


# ── Background pattern rendering ────────────────────────────────────────────

def _render_bg_pattern(procedure: str, seg_idx: int, output_path: Path) -> None:
    """Render a thematic background PNG unique to the procedure and segment."""
    colors  = PROC_COLORS.get(procedure, PROC_COLORS["laser"])
    base, a1, a2 = colors["base"], colors["a1"], colors["a2"]
    rng     = random.Random(seg_idx * 41 + abs(hash(procedure)) % 997)

    # Bilinear 4-corner gradient — corners rotate per segment for variety
    corner_sets = [
        (a1, base, base, a2),
        (a2, a1,   base, base),
        (base, a2, a1,   base),
        (base, base, a2, a1),
        (a1, a2,   base, base),
    ]
    tl, tr, bl, br = corner_sets[seg_idx % len(corner_sets)]
    small = Image.new("RGB", (2, 2))
    small.putpixel((0, 0), tl); small.putpixel((1, 0), tr)
    small.putpixel((0, 1), bl); small.putpixel((1, 1), br)
    img = small.resize((W, H), Image.BILINEAR)

    # Thematic pattern layer (transparent RGBA)
    pat  = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(pat)
    kind = PROC_PATTERNS.get(procedure, "ripples")

    if kind == "laser":
        # Diagonal grid + bright light points
        step = 80 + rng.randint(-15, 15)
        for i in range(-H, W + H, step):
            draw.line([(i, 0), (i + H, H)], fill=(*a1, 22), width=1)
        for i in range(-H, W + H, step + 25):
            draw.line([(i + H, 0), (i, H)], fill=(*a2, 14), width=1)
        for _ in range(18):
            x, y = rng.randint(0, W), rng.randint(0, H)
            for r, a in [(2, 255), (7, 90), (18, 35), (40, 10)]:
                draw.ellipse([x-r, y-r, x+r, y+r], fill=(*a2, a))

    elif kind == "ripples":
        # Concentric ripple circles from multiple drop points
        for _ in range(5):
            cx = rng.randint(80, W - 80)
            cy = rng.randint(150, H - 150)
            for r in range(40, 750, 55 + rng.randint(-8, 8)):
                alpha = max(5, 80 - r // 9)
                draw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=(*a1, alpha), width=2)

    elif kind == "layers":
        # Horizontal bands with faint edge lines (skin layers)
        n = 14
        for i in range(n):
            y1, y2 = i * H // n, (i + 1) * H // n
            c = a1 if i % 2 == 0 else a2
            mid = abs(i / n - 0.5)
            alpha = int(12 + 28 * (1 - mid * 2))
            draw.rectangle([0, y1, W, y2], fill=(*c, alpha))
            draw.line([(0, y1), (W, y1)], fill=(*a2, 20), width=1)

    elif kind == "rings":
        # Concentric ellipses (electromagnetic RF)
        cx = W // 2 + rng.randint(-80, 80)
        cy = H // 2 + rng.randint(-180, 180)
        for r in range(70, 1150, 95 + rng.randint(-10, 10)):
            alpha = max(5, 58 - r // 20)
            ry = int(r * 1.45)
            draw.ellipse([cx-r, cy-ry, cx+r, cy+ry], outline=(*a1, alpha), width=3)
            draw.ellipse([cx-r+18, cy-ry+26, cx+r-18, cy+ry-26],
                         outline=(*a2, alpha // 2), width=1)

    elif kind == "dots":
        # Regular micro-needle dot grid
        spacing = 52 + rng.randint(-4, 4)
        off = rng.randint(0, spacing)
        for x in range(off, W + spacing, spacing):
            for y in range(off, H + spacing, spacing):
                r = rng.randint(3, 7)
                c = a1 if rng.random() > 0.35 else a2
                draw.ellipse([x-r, y-r, x+r, y+r], fill=(*c, rng.randint(40, 110)))

    elif kind == "bubbles":
        # Foam bubble clusters
        for _ in range(55):
            x, y = rng.randint(0, W), rng.randint(0, H)
            r = rng.randint(10, 65)
            c = rng.choice([a1, a2])
            draw.ellipse([x-r, y-r, x+r, y+r], outline=(*c, rng.randint(18, 52)), width=2)
            if r > 22:                                  # specular highlight
                hr = max(3, r // 5)
                draw.ellipse([x-hr, y-r+hr, x+hr, y-r+hr*3], fill=(*a2, 65))

    elif kind == "glow":
        # Radial rays + central glow + gold sparkles (premium/investment)
        import math
        cx, cy = W // 2 + rng.randint(-60, 60), H // 2 + rng.randint(-120, 120)
        # Radial rays
        angle_step = rng.randint(7, 14)
        for angle in range(0, 360, angle_step):
            rad    = math.radians(angle + rng.randint(-3, 3))
            length = rng.randint(250, 800)
            x2 = cx + int(length * math.cos(rad))
            y2 = cy + int(length * math.sin(rad))
            draw.line([(cx, cy), (x2, y2)], fill=(*a1, rng.randint(8, 22)), width=1)
        # Central radial glow (bright core fading out)
        for r, alpha in [(400, 6), (260, 12), (150, 22), (75, 38), (30, 60)]:
            draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=(*a1, alpha))
        # Gold sparkle points scattered across frame
        for _ in range(28):
            x, y = rng.randint(0, W), rng.randint(0, H)
            for r, a in [(1, 255), (4, 90), (10, 28)]:
                draw.ellipse([x-r, y-r, x+r, y+r], fill=(*a2, a))

    pat = pat.filter(ImageFilter.GaussianBlur(radius=1))
    img = Image.alpha_composite(img.convert("RGBA"), pat).convert("RGB")
    img.save(str(output_path), "PNG")


# ── FFmpeg assembly ──────────────────────────────────────────────────────────

def _make_segment_bg(procedure: str, duration: float, seg_idx: int, output: Path) -> None:
    """Render thematic pattern PNG then animate with Ken Burns zoom/pan."""
    pattern = output.parent / f"pattern_{seg_idx}.png"
    _render_bg_pattern(procedure, seg_idx, pattern)

    n_frames = max(int(duration * FPS), 1)
    kb       = SEG_ZOOMS[seg_idx % len(SEG_ZOOMS)]
    scale_w  = int(W * 1.16) + (int(W * 1.16) % 2)
    scale_h  = int(H * 1.16) + (int(H * 1.16) % 2)
    vf = f"scale={scale_w}:{scale_h},zoompan={kb}:d={n_frames}:s={W}x{H}:fps={FPS}"

    subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(pattern),
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-t", str(duration),
        str(output),
    ], check=True, capture_output=True)


def _concat_bg_clips(clips: list[Path], durations: list[float], output: Path) -> None:
    """Crossfade background clips (video only). Text overlay is applied separately."""
    n = len(clips)
    inputs = []
    for c in clips:
        inputs += ["-i", str(c)]

    parts = []
    v_in  = "[0:v]"
    cumul = 0.0
    for i in range(1, n):
        cumul += durations[i-1]
        offset = max(0.05, cumul - TRANS)
        v_out  = "[vout]" if i == n - 1 else f"[v{i}]"
        parts.append(
            f"{v_in}[{i}:v]xfade=transition=fade:duration={TRANS}:offset={offset:.3f}{v_out}"
        )
        v_in = v_out

    subprocess.run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(parts),
        "-map", "[vout]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(output),
    ], check=True, capture_output=True)


def _concat_audios(audios: list[Path], output: Path) -> None:
    n = len(audios)
    inputs = []
    for a in audios:
        inputs += ["-i", str(a)]

    parts = []
    a_in  = "[0:a]"
    for i in range(1, n):
        a_raw = "[araw]" if i == n - 1 else f"[a{i}]"
        parts.append(f"{a_in}[{i}:a]acrossfade=d={TRANS}{a_raw}")
        a_in = a_raw

    # Normalize to -14 LUFS (YouTube Shorts standard) after mixing
    parts.append(f"{a_in}loudnorm=I=-14:LRA=7:TP=-1[aout]")

    subprocess.run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(parts),
        "-map", "[aout]",
        "-c:a", "aac", "-b:a", "192k",
        str(output),
    ], check=True, capture_output=True)


def _overlay_segments(
    bg: Path,
    overlays: list[Path],
    durations: list[float],
    audio: Path,
    output: Path,
) -> None:
    """Overlay transparent text PNGs on the background at audio timestamps. Text is static."""
    n = len(overlays)
    starts = [0.0]
    for i in range(1, n):
        starts.append(starts[i-1] + durations[i-1] - TRANS)
    total_dur = starts[-1] + durations[-1]

    inputs = ["-i", str(bg), "-i", str(audio)]
    for ov in overlays:
        inputs += ["-loop", "1", "-i", str(ov)]

    # streams: 0=bg, 1=audio, 2..n+1=overlay PNGs
    parts   = []
    current = "[0:v]"
    for i in range(n):
        t0  = f"{starts[i]:.3f}"
        t1  = f"{(starts[i+1] if i < n-1 else total_dur + 1):.3f}"
        out = "[vout]" if i == n - 1 else f"[v{i+1}]"
        ov_idx = i + 2
        parts.append(
            f"[{ov_idx}:v]format=rgba[ov{i}];"
            f"{current}[ov{i}]overlay=0:0:enable='between(t,{t0},{t1})'{out}"
        )
        current = out

    subprocess.run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(parts),
        "-map", "[vout]", "-map", "1:a",
        "-c:v", "libx264", "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        "-t", str(total_dur),
        str(output),
    ], check=True, capture_output=True)


def _build_video(script: dict, procedure: str, work_dir: Path, template: str = "") -> Path:
    segments = [
        ("hook",   script["hook"]),
        ("block1", script["block1"]),
        ("block2", script["block2"]),
        ("block3", script["block3"]),
        ("cta",    script["cta"]),
    ]

    # Step 1 — voiceovers with per-card prosody + breaks
    audios, durations = [], []
    for i, (card_type, text) in enumerate(segments):
        audio = work_dir / f"seg_{i}.mp3"
        _generate_voiceover(text, audio, card_type)
        audios.append(audio)
        durations.append(_get_audio_duration(audio))

    # Step 2 — transparent text overlay PNGs
    overlays = []
    for i, (card_type, text) in enumerate(segments):
        ov = work_dir / f"seg_{i}.png"
        _render_overlay(text, card_type, procedure, i, ov, template=template)
        overlays.append(ov)

    # Step 3 — per-segment animated backgrounds, then crossfade into one bg track
    bg_clips = []
    for i, (_, _) in enumerate(segments):
        bg = work_dir / f"bg_{i}.mp4"
        _make_segment_bg(procedure, durations[i], i, bg)
        bg_clips.append(bg)
    bg_out = work_dir / "bg.mp4"
    _concat_bg_clips(bg_clips, durations, bg_out)

    # Step 4 — audio track with crossfades + optional background music
    audio_out = work_dir / "audio.aac"
    _concat_audios(audios, audio_out)
    music_out = work_dir / "audio_music.aac"
    _mix_music(audio_out, music_out)
    total_dur = _get_audio_duration(music_out)

    # Step 5 — composite text overlays at correct timestamps + mux
    output = work_dir / "short.mp4"
    _overlay_segments(bg_out, overlays, durations, music_out, output)

    return output


def should_skip_slot() -> tuple[bool, str]:
    """Smart skip для auto-publish: пропустить слот если условия плохие.
    Returns (skip, reason).

    Сигналы:
    1. Свежих (procedure, template) комбинаций осталось < 5 — приближаемся к исчерпанию,
       растягиваем оставшиеся (не начинаем повторяться раньше времени).
    2. Средний AVD последних 10 опубликованных шортов < 22% — алгоритм охладел,
       не наваливаем больше пока не восстановится.
    """
    queue = load_queue()
    used = {
        (i["procedure"], i["template"])
        for i in queue
        if i.get("type") == "short" and i.get("status") != "rejected"
        and i.get("procedure") and i.get("template")
    }
    all_combos: list[tuple[str, str]] = []
    proc_templates = [p for p in PROCEDURES if p != "general"]
    for p in proc_templates:
        for t in TEMPLATES:
            if t in TEMPLATE_BLACKLIST:
                continue
            all_combos.append((p, t))
    for t in GENERAL_TEMPLATES:
        if t in TEMPLATE_BLACKLIST:
            continue
        all_combos.append(("general", t))
    fresh = [c for c in all_combos if c not in used]
    if len(fresh) < 5:
        return True, f"осталось {len(fresh)} свежих комбо (растягиваем)"

    # AVD signal
    try:
        analytics_path = BASE_DIR / "analytics.json"
        if analytics_path.exists():
            data = json.loads(analytics_path.read_text(encoding="utf-8"))
            shorts = [
                v for v in data.get("videos", {}).values()
                if v.get("is_short") and v.get("avg_view_pct") is not None
            ]
            shorts.sort(key=lambda v: v.get("published_at", ""), reverse=True)
            last_n = shorts[:10]
            if len(last_n) >= 5:
                avg_avd = sum(v["avg_view_pct"] for v in last_n) / len(last_n)
                if avg_avd < 22.0:
                    return True, f"avg AVD последних {len(last_n)} = {avg_avd:.1f}% (<22%)"
    except Exception as e:
        logger.warning("should_skip_slot: ошибка чтения analytics — %s", e)

    return False, ""


def _next_unique_combo() -> tuple[str, str]:
    """Pick a (procedure, template) pair not yet used in the queue (excl. rejected)."""
    queue = load_queue()
    used  = {
        (i["procedure"], i["template"])
        for i in queue
        if i.get("type") == "short" and i.get("status") != "rejected"
        and i.get("procedure") and i.get("template")
    }

    # Build all valid combos: each procedure × its template pool (exclude blacklisted)
    all_combos: list[tuple[str, str]] = []
    proc_templates = [p for p in PROCEDURES if p != "general"]
    for p in proc_templates:
        for t in TEMPLATES:
            if t in TEMPLATE_BLACKLIST:
                continue
            all_combos.append((p, t))
    for t in GENERAL_TEMPLATES:
        if t in TEMPLATE_BLACKLIST:
            continue
        all_combos.append(("general", t))

    unused = [c for c in all_combos if c not in used]

    if unused:
        # Prefer procedures not appearing in last 4 shorts
        recent_procs = [i["procedure"] for i in queue[-4:]
                        if i.get("type") == "short" and i.get("status") != "rejected"]
        fresh = [c for c in unused if c[0] not in recent_procs]
        pool  = fresh if fresh else unused
        return random.choice(pool)

    # All combos used — pick least recently used
    used_ordered = [
        (i["procedure"], i["template"])
        for i in queue
        if i.get("type") == "short" and i.get("procedure") and i.get("template")
    ]
    if used_ordered:
        return used_ordered[0]
    return random.choice(all_combos)


def generate_short(procedure: str = None, template: str = None) -> dict:
    # General templates require the "general" pseudo-procedure
    if template and template in GENERAL_TEMPLATES:
        procedure = "general"

    # Auto-pick unique combo if neither arg given
    if not procedure and not template:
        procedure, template = _next_unique_combo()
    elif not procedure:
        queue  = load_queue()
        recent = [i["procedure"] for i in queue[-8:] if i.get("type") == "short"]
        unused = [p for p in PROCEDURES if p not in recent]
        procedure = random.choice(unused) if unused else random.choice(PROCEDURES)
    elif not template:
        queue = load_queue()
        if procedure == "general":
            pool     = GENERAL_TEMPLATES
            recent_t = [i.get("template") for i in queue[-len(pool):]
                        if i.get("type") == "short" and i.get("procedure") == "general"]
        else:
            pool     = TEMPLATES
            recent_t = [i.get("template") for i in queue[-len(pool):]
                        if i.get("type") == "short" and i.get("procedure") != "general"]
        unused_t = [t for t in pool if t not in recent_t]
        template = unused_t[0] if unused_t else random.choice(list(pool))

    item_id  = str(uuid.uuid4())[:8]
    work_dir = SHORTS_DIR / item_id
    work_dir.mkdir()

    logger.info("Генерирую Short [%s] %s / %s", item_id, procedure, template)

    script     = _generate_script(procedure, template)
    video_path = _build_video(script, procedure, work_dir, template=template)

    final_path = SHORTS_DIR / f"{item_id}.mp4"
    video_path.rename(final_path)
    for f in work_dir.iterdir():
        f.unlink()
    work_dir.rmdir()

    item = {
        "id":                item_id,
        "type":              "short",
        "procedure":         procedure,
        "template":          template,
        "script":            script,
        "video_path":        str(final_path),
        "status":            "pending",
        "created_at":        datetime.datetime.now().isoformat(),
        "review_message_id": None,
    }

    queue = load_queue()
    queue.append(item)
    save_queue(queue)

    logger.info("Short готов: %s", final_path)
    return item


def generate_series(series_key: str) -> list[dict]:
    """Generate all 3 parts of a named series. Returns list of items."""
    parts = SERIES.get(series_key)
    if not parts:
        raise ValueError(f"Неизвестная серия: {series_key}. Варианты: {', '.join(SERIES)}")
    items = []
    for procedure, template in parts:
        item = generate_short(procedure, template)
        items.append(item)
    logger.info("Серия '%s' готова: %d шортов", series_key, len(items))
    return items


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    proc = sys.argv[1] if len(sys.argv) > 1 else None
    tmpl = sys.argv[2] if len(sys.argv) > 2 else None
    item = generate_short(proc, tmpl)
    print(f"Готово: {item['video_path']}")
    print(json.dumps(item["script"], ensure_ascii=False, indent=2))
