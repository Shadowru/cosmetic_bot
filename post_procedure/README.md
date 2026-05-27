# Post-Procedure Content Bot

Telegram-бот + автогенерация контента для канала **@posleprocedur**.  
Публикует YouTube Shorts и текстовые посты о домашнем уходе после косметологических процедур.

---

## Быстрый старт

```bash
cp .env.example .env
# заполни BOT_TOKEN, OWNER_ID, OLLAMA_MODEL в .env

pip install -r requirements.txt
pip install torch silero num2words omegaconf tiktok-uploader qrcode openai-whisper yt-dlp
python3 -m playwright install chromium   # для TikTok-загрузки

# Авторизация YouTube (один раз, scope `youtube.force-ssl` + `yt-analytics.readonly`)
python3 youtube_auth.py

# Запуск бота
source .env && export BOT_TOKEN OWNER_ID OLLAMA_MODEL
nohup python3 bot.py >> /tmp/bot.log 2>&1 &
```

---

## Переменные окружения (`.env`)

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен бота от @BotFather |
| `OWNER_ID` | Telegram user_id владельца (только он управляет ботом) |
| `OLLAMA_MODEL` | Модель Ollama, по умолчанию `qwen2.5:14b` |
| `PROXYAPI_KEY` | (опционально) Ключ от [proxyapi.ru](https://proxyapi.ru) для Gemini fact-check шаблона `norm_alarm`. Без него валидация пропускается (graceful degradation). |
| `PROXYAPI_MODEL` | (опционально) Модель для валидатора, default `gemini-3.5-flash` |

---

## Зависимости

| Пакет | Зачем |
|---|---|
| `python-telegram-bot[job-queue]` | Telegram Bot API |
| `requests` | HTTP к Ollama |
| `pillow` | Рендеринг кадров видео |
| `numpy`, `scipy` | Аудиообработка (WAV) |
| `torch` | Silero TTS |
| `silero` (через torch.hub) | Русский TTS |
| `num2words` | Числа → текст для TTS |
| `omegaconf` | Зависимость Silero |
| `google-api-python-client` и др. | YouTube Data API v3 |
| `tiktok-uploader` | Загрузка в TikTok через cookies (chromium через Playwright) |
| `qrcode` | QR-код на CTA-карточке шортов |
| `openai-whisper` | Транскрипция аудио конкурентных шортов для `/research` |
| `yt-dlp` | Скачивание аудио YouTube для `/research` |
| `ffmpeg` (системный) | Сборка видео, наложение аудио |

---

## Архитектура

```
Ollama (qwen2.5:14b)
    ↓ generate_script / generate_post
generator.py          — текстовые посты
shorts_generator.py   — сценарий → кадры → TTS → видео
    ↓
content_queue.json    — очередь на ревью

bot.py (Telegram)
    → /generate / /today_gen → генерация
    → ревью-клавиатура (✅ / 📅 / ❌ / 🔄)
    → публикация в @posleprocedur
    → upload в YouTube + TikTok

content_plan.py
    → content_plan.json   — месячный план (2 шорта + пост/день)
```

---

## Команды бота

### Контент-план

| Команда | Действие |
|---|---|
| `/plan` | Показать план текущего месяца (календарь со статусами ⬜🟡📅✅) |
| `/plan new` | Сгенерировать план на текущий месяц |
| `/today` | Задачи на сегодня из плана |
| `/today_gen` | Сгенерировать всё pending на сегодня и отправить на ревью |

Навигация в плане: кнопки ◀ / ▶ для переключения месяцев, «🗓 Создать план» для генерации.

### Ручная генерация

| Команда | Действие |
|---|---|
| `/generate short [процедура] [шаблон]` | Сгенерировать шорт |
| `/generate post [процедура] [тип]` | Сгенерировать текстовый пост |
| `/series [название]` | Сгенерировать 3-частную серию шортов |
| `/next` | Сгенерировать **следующий уникальный** шорт (комбинация процедура×шаблон ещё не использовалась) |
| `/queue` | Показать очередь на ревью |

Порядок аргументов не важен, бот автоматически определяет что есть что.

Пример: `/generate short biorevit signs`

### Авто-публикация без ревью

| Команда | Действие |
|---|---|
| `/autopublish` | **Toggle on/off** — переключает режим. Когда включён, каждый день в 08:00, 11:00, 14:00, 17:00, 20:00 МСК бот **проверяет smart-skip** (см. ниже), если условия ОК — генерирует уникальный шорт через `_next_unique_combo()` и публикует в YouTube + TikTok |

Состояние хранится в `autopublish.flag` (файл существует → включено). Никакого истечения — работает пока не выключишь.

**Smart-skip** (`shorts_generator.should_skip_slot()`): слот пропускается если ИЛИ свежих `(процедура, шаблон)` комбинаций осталось < 5 (растягиваем оставшиеся, не начинаем рано повторяться), ИЛИ средний AVD последних 10 опубликованных шортов < 22% (алгоритм охладел, даём восстановиться). Логируется в `bot.log` строкой `Auto-publish slot skipped: …`. Уведомления в TG не шлются, чтобы не дёргать.

⚠️ В коде есть флаг `CROSSPOST_SHORTS_TO_TG = False` (`bot.py`) — шорты **не** дублируются в TG-канал чтобы канал имел уникальный контент (lead-magnets, текстовые посты). Если захочешь вернуть кросспост — поставить True.

### Lead-magnets для Telegram-канала

| Команда | Действие |
|---|---|
| `/post_magnets` | Сгенерировать через Ollama детальные «Календарь восстановления после X» для всех процедур, опубликовать в канал, попытаться закрепить |
| `/post_magnets <процедура>` | Перегенерировать конкретный магнит (если не понравился) |

Магниты — это закреплённые посты в TG-канале (~1800-3200 символов), которые служат **lead-магнитом для подписки**: зритель приходит из YouTube по QR, видит закреп с детальным календарём по своей процедуре, подписывается. Кэш в `lead_magnets.json`. Файл `lead_magnets.py` — модуль.

### Instagram Reels (manual)

| Команда | Действие |
|---|---|
| `/insta [N]` | Прислать топ-N (по умолч. 3) видео за последние 14 дней — сортировка по `views × (AVD/100)`. Каждое присылается как файл с метаданными для скачивания. Помечает в queue `ig_selected_at` чтобы не повторять |
| `/insta reset` | Сбросить пометки — все видео снова доступны для отбора |

Idea: автоматизация постинга в Instagram нежелательна (бан-риски, низкая конверсия из заблокированной в РФ платформы). Лучше отбирать топ руками через `/insta` и заливать в Reels вручную.

### Аналитика и исследования

| Команда | Действие |
|---|---|
| `/stats` | Сохранённая статистика канала (просмотры, шаблоны, процедуры, топ-5 шортов с retention) |
| `/stats refresh` | Обновить с YouTube Data + Analytics API, сразу прислать AI-инсайты недели от Ollama |
| `/research` | Исследование топовых конкурентных шортов на 4 языках (ru/en/ko/fr) — структура, хуки, формулы |
| `/research transcribe` | То же + транскрипция аудио через Whisper (медленнее, но точнее) |

Аналитика автоматически обновляется раз в неделю (job `weekly_analytics`) и присылается в личку владельцу. Skip-логика: если данные младше 6 дней — пропускает.

### Ревью-клавиатура

После генерации бот присылает контент с кнопками:

- **✅ Сейчас** — немедленная публикация в канал + загрузка на YouTube и TikTok
- **📅 Запланировать** — загрузка на YouTube с отложенной датой (пн/ср/пт 09:00 МСК), публикация в канал в назначенное время
- **❌ Пропустить** — отклонить без публикации
- **🔄 Переделать** — генерация заново с теми же параметрами

---

## Процедуры и шаблоны

### Процедуры (13)

Базовые (10): `laser` · `biorevit` · `piling` · `rf` · `dermaroller` · `chistka` · `meso` · `botox` · `fillers` · `general`

Расширение (3, добавлены 2026-05-23): `plazma` (плазмолифтинг/PRP) · `photo` (фотоомоложение IPL) · `smas` (SMAS-лифтинг)

Все процедуры (кроме `general`) имеют уникальный цвет (`PROC_COLORS`), геометрический паттерн (`PROC_PATTERNS`), серию (`SERIES`), склонения (`PROC_NAMES`/`PROC_NAMES_NOM`).

### Шаблоны шортов (процедурные, 15)

| Ключ | Тема | Заметка |
|---|---|---|
| `mistakes` | Ошибки которые делают все | |
| `days` | Первые 3 дня — что реально нужно делать | **топ по AVD (39.6%)** |
| `forbidden` | Что нельзя делать | |
| `vs` | Аптечный крем vs профессиональная косметика | |
| `signs` | Как понять что кожа заживает правильно | |
| `myths` | Мифы про уход | |
| `speed` | Как ускорить заживление | |
| `repeat` | Через сколько снова делать процедуру | |
| `nobody` | Никто не говорит вслух | |
| `first_time` | Делаешь впервые — вот что тебя ждёт | |
| `day_in_life` | День после процедуры — что я делаю с утра до вечера | от первого лица, таймстемпы |
| `days_7` | Неделя после процедуры — что меняется по дням | timeline family |
| `day_vs` | День 1 vs День 7 после процедуры | timeline family |
| `recovery_calendar` | Календарь восстановления — 14 дней по шагам | timeline family |
| `norm_alarm` | **Норма или тревога после X** — отличаем за 30 секунд | 2 нормы + 1 тревожный признак |

`TEMPLATE_BLACKLIST` (`shorts_generator.py`) — пустой set. Раньше блокировали `first_time` по старым данным, но после внедрения кикера и нумерации retention вырос — вернули в ротацию.

### Шаблоны general (8 универсальных)

`money` · `sun` · `timing` · `retinol` · `universal` · `reactions` · `ingredients` · `budget`

Для general-шаблонов отдельный `GENERAL_HOOK_STYLE` с warning-тоном (страх потери, срочность) — общие темы без процедуры нуждаются в более жёстких хуках чтобы цеплять.

### Серии шортов (`/series`)

12 серий по 3 ролика (один на каждую процедуру, исключая `general`). Третий ролик в каждой серии — `norm_alarm`. Это создаёт у зрителя ожидание продолжения.

**Визуализация серий**: на хук-карточке появляется плашка `СЕРИЯ · N/3` в левом верхнем углу — если видео является частью какой-либо серии. Helper `_series_position(procedure, template)` ищет позицию. Цель — повысить subscriber rate (нужно подписаться чтобы найти продолжение) и returning viewer rate.

### Типы постов (12)

`educational` · `myth` · `practical` · `qa` · `light` · `engagement` · `signs` · `investment` · `warning` · `universal` · `nobody` · `budget`

---

## Месячный контент-план

**Логика планирования:**
- 2 шорта в день — слоты 06:00 UTC (09:00 МСК) и 16:00 UTC (19:00 МСК)
- 1 пост в день — слот 08:00 UTC (11:00 МСК)
- Каждый 5-й слот шорта → general тема (~20% контента)
- Процедуры ротируются без повторений в соседних слотах
- Шаблоны ротируются по процедуре до полного прохождения всех

**Статусы:** `pending` → `generated` → `published` / `scheduled`

**Файл:** `content_plan.json` — структура по `YYYY-MM` → `YYYY-MM-DD` → `{shorts: [...], post: {...}}`

---

## Пайплайн генерации шорта

1. **Сценарий** — Ollama генерирует JSON: `{hook, block1, block2, block3, cta}`, 45–60 слов. Структура хука: **curiosity_gap + обещание "3 [правила/признака/ошибки]"** (доказано — даёт топовый retention).
2. **Кадры** — Pillow рисует 5 кадров (1080×1920) с дизайн-системой:
   - **Hook**: **визуальный кикер** наверху (СТОП. / ОШИБКА. / ТРЕВОГА? / МИФ. / НОРМА? — словарь `KICKER`, по шаблону) — гигантский шрифт 120pt, светлый цвет акцента на тёмной подложке. Срабатывает в первые 0.5s, бьёт по «scroll-past». Ниже — текст хука. Если видео — часть серии, в левом верхнем углу плашка **«СЕРИЯ · N/3»**.
   - **block1/2/3**: текст + **прогресс-бейдж 1/3 → 2/3 → 3/3** в верхнем углу. Незакрытый список держит до конца. **block3 — punchline**: самый сильный пункт + микро-вопрос к зрителю в конце (engagement-сигнал на пике удержания).
   - **CTA**: «**ЗАБИРАЙ →**» крупно сверху (как кикер, но позитивный — фрейм ценности, не прощания) + текст ценности + **QR-код** (172×172, белый бордюр для скана) + «**📸 СОХРАНИ**» бейдж под QR. Передаёт ценность без вопроса (вопрос уже в block3) — ритм не провисает, viewer-drop в финале минимальный.
   - **CTA-обещание** конкретное: «**Календарь по дням** после X — в закрепе Telegram», не «полный список». Магнит должен совпадать с тем что зритель увидит в TG (`lead_magnets.py`).
3. **TTS** — Silero (speaker `xenia`, 48kHz). Препроцессинг: числа → `num2words(ru)`, латинские аббревиатуры (SPF/UV/pH/AHA/BHA/RF/PRP и др.) → транскрипция. Синтез **предложение-за-предложением** с 120ms тишины между ними (иначе Silero рвёт речь на швах).
4. **Темп** — FFmpeg `atempo` по карточке: `hook 0.93×`, `block1/2 0.88×`, `block3 0.85×`, `cta 0.82×`. Хук быстрее (захват), к концу медленнее (для веса).
5. **Музыка** — случайный lo-fi трек из `music/` через `amix` на ~18% громкости.
6. **Сборка** — FFmpeg `concat` + `xfade` между сегментами → MP4 (9:16, 1080×1920, 25 FPS).

**Визуальные паттерны** (уникальны для каждой процедуры):
`laser` grid · `biorevit/meso/plazma` ripples · `piling` layers · `rf/botox/smas` rings · `dermaroller` dots · `chistka` bubbles · `fillers/general/photo` glow

**Отдельный системный промпт для `day_in_life`**: формат от первого лица с таймстемпами (`9 утра — умываюсь...` / `вечер — ...`), не curiosity_gap. Используется `DAY_IN_LIFE_SCRIPT_SYSTEM`, кикер выключен.

---

## Публикация

### YouTube Shorts
- `youtube_auth.py` — OAuth2 авторизация (один раз). Scope: **`youtube.force-ssl` + `yt-analytics.readonly`** (force-ssl нужен для постинга комментариев, обычного `youtube` scope НЕ хватает)
- `youtube_uploader.py` — загрузка через YouTube Data API v3 + **авто-публикация закреплённого комментария**: сразу после загрузки бот постит вопрос-завлекалку от лица канала (по процедуре, `_COMMENT_QUESTIONS`) — это сигнал алгоритму на engagement. Ссылку на Telegram **специально не пихаем в коммент** — это уводит с YouTube. Pin вручную через Studio (API не умеет закреплять).
- «Сейчас»: публикуется через 1 час (задержка `YOUTUBE_DELAY=3600`)
- «Запланировать»: загружается сразу как `private` с `publishAt`, виден в Studio сразу

### TikTok
- `tiktok_cookies.json` — cookies из браузера (JSON-формат)
- `tt_uploader.py` → **`tt_upload_worker.py`** subprocess — загрузка через `tiktok-uploader` в отдельном процессе (см. «Технические решения»)
- Браузер: **`chromium` через Playwright** (`python3 -m playwright install chromium`), не системный Chrome
- Публикуется параллельно с Telegram при «✅ Сейчас» и при наступлении запланированного времени

### Аналитика YouTube
- `yt_stats.py` — собирает статистику через YouTube Data API + **YouTube Analytics API** (retention `averageViewPercentage`, CTR, watch time)
- `analytics.json` — кэш всех данных, обновляется через `/stats refresh` или раз в неделю автоматически
- `weekly_insights()` — Ollama-анализ топ-видео недели: что сработало, 3 рекомендации, чего избегать
- `get_template_weights()` — возвращает множители для весов в `content_plan.py` (топ-шаблон → ×2.0, аутсайдер → ×0.5). Авто-планировщик чаще выбирает работающие шаблоны.

### Fact-check для шаблона `norm_alarm`
- `validators.py` — модуль с `validate_norm_alarm(script, procedure_nom)`
- Под капотом: Gemini 3.5 Flash через **`api.proxyapi.ru/google/v1beta/...`** (нативный Google API заблокирован FAILED_PRECONDITION в РФ-регионе)
- Проверяет: правда ли «норма» это норма, правда ли «тревога» — повод к врачу, корректны ли сроки
- В `_generate_script`: при шаблоне `norm_alarm` → fact-check → если `fail` регенерация до 3 раз → если все 3 фейлятся, генерация падает с ValueError
- `severity=warn` — проходит, но в WARN-лог
- Стоимость: ~$0.0002 за проверку, 2-5¢/мес на наших объёмах
- Graceful degradation: без `PROXYAPI_KEY` валидация пропускается, контент проходит как обычно

### Конкурентное исследование
- `yt_research.py` — поиск топ-шортов по теме на ru/en/ko/fr, скачивание аудио, опциональная транскрипция через Whisper (`task="translate"` → английский), Ollama-анализ структуры (hook_type, structure, cta_style, reusable_formula)
- Результат — JSON со списком шаблонных формул, плюс summary для бота
- Это давало вывод: **`curiosity_gap` доминирует** в виральных шортах (5/10), что и зашито в `SCRIPT_SYSTEM`

### Telegram

**Бот `@nenaugad_bot`** — это и есть `bot.py` (один процесс). Двойная роль:
- Для **владельца** (`OWNER_ID`): админка генерации/ревью/публикации
- Для **публичных пользователей**: `/start` → выбор процедуры → фаза → продукты. После подбора набора пользователю показывается inline-кнопка «**📅 Подробный календарь в канале**» → ведёт на `t.me/posleprocedur`. В тексте также CTA «Подпишись на канал → закрепы с календарём».

**Канал `@posleprocedur`** — уникальный контент:
- Шорты **не дублируются** (`CROSSPOST_SHORTS_TO_TG = False`)
- Текстовые посты от `daily_generate` (1/день 09:00 МСК)
- **Lead-magnets** в закрепе: «Календарь восстановления после X — 14 дней» по каждой процедуре (генерится через `/post_magnets`)
- Промо-пост раз в 6 часов про подбор набора через `@nenaugad_bot`

Воронка:
```
YouTube Shorts → QR/«ЗАБИРАЙ →» в CTA → @posleprocedur
                                          ↓ закрепы (lead-magnets)
                                          ↓ подпиcка
              → клик «Подобрать набор» → @nenaugad_bot → выбор процедуры → набор + ссылка обратно на канал
```

---

## Файловая структура

```
post_procedure/
├── bot.py                  # Telegram-бот, логика ревью и публикации
├── generator.py            # Генерация текстовых постов
├── shorts_generator.py     # Генерация видео-шортов (TTS + PIL + FFmpeg) + кикеры/QR/бейджи
├── content_plan.py         # Месячный план контента
├── youtube_uploader.py     # Загрузка на YouTube + авто-коммент
├── youtube_auth.py         # OAuth2 для YouTube (запустить один раз)
├── tt_uploader.py          # Обёртка для TikTok-загрузки
├── tt_upload_worker.py     # Subprocess worker для TikTok (изолирует Playwright sync API)
├── yt_stats.py             # Сбор статистики канала + YouTube Analytics API + Ollama insights
├── yt_research.py          # Анализ конкурентных шортов (мультиязычный, Whisper + Ollama)
├── lead_magnets.py         # Генерация календарей восстановления (закреплённые посты TG)
├── validators.py           # Gemini fact-check для norm_alarm (через proxyapi.ru, graceful degradation)
├── keyword_research.py     # Сбор ключевых слов через Yandex Suggest
├── admin.py                # Утилиты для ручного управления очередью
│
├── products.json           # Процедуры и продукты для клиентского бота
├── content_queue.json      # Очередь контента (runtime)
├── content_plan.json       # Месячный план (runtime)
├── channel_state.json      # Флаг для промо-постов (runtime)
├── analytics.json          # Кэш статистики канала (runtime)
├── autopublish.flag        # Toggle: файл существует → авто-публикация включена (runtime)
├── lead_magnets.json       # Кэш сгенерированных календарей восстановления (runtime)
│
├── shorts/                 # Сгенерированные видео (runtime)
├── research_audio/         # Аудио конкурентных видео для /research (runtime)
├── music/                  # Lo-fi треки для фона (5 файлов)
│
├── .env                    # Секреты (в .gitignore)
├── .env.example
├── requirements.txt
│
├── index.html              # Главная страница сайта
├── website_plan.md         # Архитектура сайта и SEO-план
├── post_procedure_plan.md  # Бизнес-план (рынок, финансы, роадмап)
├── keyword_report.xlsx     # 82 ключевых запроса с приоритетами
├── tg_posts_week1.md       # Посты для Telegram, неделя 1
└── ingredients_and_skus.md # База ингредиентов и SKU
```

---

## Добавление новой процедуры

1. `shorts_generator.py` — добавить в `PROCEDURES`, `PROC_NAMES` (родительный), `PROC_NAMES_NOM` (именительный), `PROC_COLORS`, `PROC_PATTERNS`, `SERIES`
2. `generator.py` — добавить в `PROCEDURES`, `PROCEDURE_NAMES`
3. `youtube_uploader.py` — добавить в `_PROC_GENITIVE` (склонения для авто-комментов) и `_COMMENT_QUESTIONS` (engagement-вопрос для этой процедуры)
4. `lead_magnets.py` — добавить в `PROCEDURES` (название с пояснением для генерации календаря)
5. `products.json` — добавить карточку с фазами и продуктами (опционально, если хотим публичный flow в боте)

## Добавление нового шаблона шорта

1. `shorts_generator.py` — добавить в `TEMPLATES` (или `GENERAL_TEMPLATES`)
2. `shorts_generator.py` — подсказка в `HOOK_STYLE` (для процедурных) или `GENERAL_HOOK_STYLE` (для общих)
3. `shorts_generator.py` — кикер в `KICKER` (1-2 слова заглавными, с точкой)
4. Если нужна **отдельная структура сценария** (как у `day_in_life`) — добавить свой системный промпт и обработать в `_generate_script()`
5. `content_plan.py` подхватит автоматически (использует `sg.TEMPLATES` минус `TEMPLATE_BLACKLIST`)

---

## Перезапуск бота

```bash
kill $(ps aux | grep "python3 bot.py" | grep -v grep | awk '{print $2}') 2>/dev/null
sleep 1
source .env && export BOT_TOKEN OWNER_ID OLLAMA_MODEL
nohup python3 bot.py >> /tmp/bot.log 2>&1 &
tail -f /tmp/bot.log
```
