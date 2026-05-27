import asyncio
import datetime
import json
import os
import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

import generator
import shorts_generator
import youtube_uploader
import tt_uploader
import content_plan
import yt_stats
import yt_research
import lead_magnets

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

from pathlib import Path

PRODUCTS_FILE       = os.path.join(os.path.dirname(__file__), "products.json")
STATE_FILE          = os.path.join(os.path.dirname(__file__), "channel_state.json")
AUTOPUBLISH_FILE    = Path(__file__).parent / "autopublish.flag"
CHANNEL_ID     = "@posleprocedur"
PROMO_TEXT     = "Бот для подбора наборов — @nenaugad_bot"
YOUTUBE_DELAY  = 3600   # секунд до публикации на YouTube после одобрения
POST_INTERVAL  = 6 * 3600
OWNER_ID       = int(os.environ.get("OWNER_ID", "0"))

# TG-канал должен быть уникальным (текстовые посты + lead-magnet закрепы),
# а не дублём YouTube. Шорты в канал НЕ постим — это убивает причину подписки.
CROSSPOST_SHORTS_TO_TG = False

# Публикация по расписанию: пн/ср/пт в 09:00 МСК (06:00 UTC)
SCHEDULE_DAYS  = [0, 2, 4]   # Monday=0, Wednesday=2, Friday=4
SCHEDULE_HOUR  = 6            # UTC


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"human_posted_since_bot": True}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def load_products() -> dict:
    with open(PRODUCTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def procedure_keyboard(products: dict) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(data["name"], callback_data=f"proc:{code}")]
        for code, data in products.items()
    ]
    return InlineKeyboardMarkup(buttons)


def phase_keyboard(proc_code: str, proc_data: dict) -> InlineKeyboardMarkup:
    buttons = []
    if "acute" in proc_data:
        buttons.append([InlineKeyboardButton(
            f"🔴 {proc_data['acute']['label']}",
            callback_data=f"phase:{proc_code}:acute"
        )])
    if "recovery" in proc_data:
        buttons.append([InlineKeyboardButton(
            f"🟡 {proc_data['recovery']['label']}",
            callback_data=f"phase:{proc_code}:recovery"
        )])
    buttons.append([InlineKeyboardButton("← Назад", callback_data="back:start")])
    return InlineKeyboardMarkup(buttons)


def products_keyboard(proc_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Подробный календарь в канале", url="https://t.me/posleprocedur")],
        [InlineKeyboardButton("← К фазам", callback_data=f"proc:{proc_code}")],
    ])


def review_keyboard(item_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Сейчас",      callback_data=f"rv:ok:{item_id}"),
            InlineKeyboardButton("📅 Запланировать", callback_data=f"rv:sched:{item_id}"),
        ],
        [
            InlineKeyboardButton("❌ Пропустить",  callback_data=f"rv:no:{item_id}"),
            InlineKeyboardButton("🔄 Переделать",  callback_data=f"rv:re:{item_id}"),
        ],
    ])


def _plan_nav_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    prev = (datetime.date(year, month, 1) - datetime.timedelta(days=1))
    nxt  = (datetime.date(year, month, 28) + datetime.timedelta(days=4)).replace(day=1)
    cur_key  = f"{year:04d}-{month:02d}"
    prev_key = f"{prev.year:04d}-{prev.month:02d}"
    next_key = f"{nxt.year:04d}-{nxt.month:02d}"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("◀", callback_data=f"plan_nav:{prev_key}"),
        InlineKeyboardButton("🗓 Создать план", callback_data=f"plan_new:{cur_key}"),
        InlineKeyboardButton("▶", callback_data=f"plan_nav:{next_key}"),
    ]])


def format_phase_text(proc_name: str, phase_data: dict) -> str:
    lines = [f"<b>{proc_name}</b>", f"<i>{phase_data['label']}</i>", ""]

    any_links = False
    for step in phase_data["steps"]:
        lines.append(f"<b>{step['step']}</b>")
        if step.get("note"):
            lines.append(f"<i>{step['note']}</i>")
        for product in step["products"]:
            wb = product.get("wb_url", "")
            ozon = product.get("ozon_url", "")
            if wb or ozon:
                any_links = True
                parts = []
                if wb:
                    parts.append(f'<a href="{wb}">WB</a>')
                if ozon:
                    parts.append(f'<a href="{ozon}">Ozon</a>')
                lines.append(f"• {product['name']} — {' · '.join(parts)}")
            else:
                lines.append(f"• {product['name']}")
        lines.append("")

    if not any_links:
        lines.append("<i>Ссылки появятся совсем скоро</i>")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append(f"📅 <b>Подробный календарь по дням</b> — закрепом в канале <a href=\"https://t.me/posleprocedur\">@posleprocedur</a>")
    lines.append("Каждый день — новый разбор. <b>Подпишись</b>, чтобы не пропустить свой период.")

    return "\n".join(lines)


def _next_schedule_slot() -> datetime.datetime:
    """Find next Mon/Wed/Fri at 09:00 MSK (06:00 UTC) from now."""
    now = datetime.datetime.utcnow()
    candidate = now.replace(hour=SCHEDULE_HOUR, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += datetime.timedelta(days=1)
    for _ in range(14):
        if candidate.weekday() in SCHEDULE_DAYS:
            # Check no other item already scheduled at this exact slot
            queue = generator.load_queue()
            taken = {i.get("scheduled_at") for i in queue if i.get("scheduled_at")}
            slot_str = candidate.strftime("%Y-%m-%dT%H:%M:%S")
            if slot_str not in taken:
                return candidate
        candidate += datetime.timedelta(days=1)
    return candidate


def _first_cap(s: str) -> str:
    """Capitalize ТОЛЬКО первый символ, не трогая остальные (в отличие от .capitalize()
    которая ломает аббревиатуры вроде «SMAS» → «Smas» и «RF» → «Rf»)."""
    return s[:1].upper() + s[1:] if s else s


def _short_title(item: dict) -> str:
    proc_name = shorts_generator.PROC_NAMES.get(item["procedure"], item["procedure"])
    all_tpl   = {**shorts_generator.TEMPLATES, **shorts_generator.GENERAL_TEMPLATES}
    return _first_cap(all_tpl[item["template"]].format(proc=proc_name))


async def _upload_to_youtube_bg(bot, item: dict, publish_at=None) -> None:
    try:
        title = _short_title(item)
        url   = await asyncio.to_thread(
            youtube_uploader.upload_short, item["video_path"], title, item["procedure"], publish_at
        )
        # Save YouTube URL back to queue so yt_stats can correlate template → views
        queue = generator.load_queue()
        for q in queue:
            if q["id"] == item["id"]:
                q["yt_url"] = url
                break
        generator.save_queue(queue)

        if publish_at:
            time_str = publish_at.strftime("%d.%m %H:%M UTC")
            await bot.send_message(chat_id=OWNER_ID, text=f"▶️ YouTube запланирован на {time_str}: {url}")
        else:
            await bot.send_message(chat_id=OWNER_ID, text=f"▶️ YouTube: {url}")
    except Exception as e:
        logger.warning("YouTube upload failed: %s", e)
        await bot.send_message(chat_id=OWNER_ID, text=f"⚠️ YouTube не опубликован: {e}")


async def _upload_to_tiktok_bg(bot, item: dict) -> None:
    if not tt_uploader.is_authorized():
        return
    try:
        title = _short_title(item)
        url   = await asyncio.to_thread(tt_uploader.upload_short, item["video_path"], title)
        await bot.send_message(chat_id=OWNER_ID, text=f"🎵 TikTok: {url}")
    except Exception as e:
        logger.warning("TikTok upload failed: %s", e)
        await bot.send_message(chat_id=OWNER_ID, text=f"⚠️ TikTok не опубликован: {e}")


async def send_review(bot, item: dict) -> None:
    if item.get("type") == "short":
        await _send_short_review(bot, item)
    else:
        await _send_post_review(bot, item)


async def _send_post_review(bot, item: dict) -> None:
    proc_name = generator.PROCEDURE_NAMES.get(item["procedure"], item["procedure"])
    header = f"📝 {item['post_type']} / {proc_name}\n\n"
    msg = await bot.send_message(
        chat_id=OWNER_ID,
        text=header + item["text"],
        reply_markup=review_keyboard(item["id"]),
    )
    _set_review_msg_id(item["id"], msg.message_id)


async def _send_short_review(bot, item: dict) -> None:
    proc_name = shorts_generator.PROC_NAMES_NOM.get(item["procedure"], item["procedure"])
    all_tpl = {**shorts_generator.TEMPLATES, **shorts_generator.GENERAL_TEMPLATES}
    yt_title = _first_cap(all_tpl[item["template"]].format(proc=proc_name))
    script = item["script"]
    caption = (
        f"🎬 {item['template']} / {proc_name}\n"
        f"▶️ YT: «{yt_title}»\n\n"
        f"Hook: {script['hook']}\n\n"
        f"1: {script['block1']}\n\n"
        f"2: {script['block2']}\n\n"
        f"3: {script['block3']}\n\n"
        f"CTA: {script['cta']}"
    )
    with open(item["video_path"], "rb") as f:
        msg = await bot.send_video(
            chat_id=OWNER_ID,
            video=f,
            caption=caption[:1024],
            reply_markup=review_keyboard(item["id"]),
            supports_streaming=True,
        )
    _set_review_msg_id(item["id"], msg.message_id)


def _set_review_msg_id(item_id: str, message_id: int) -> None:
    queue = generator.load_queue()
    for q_item in queue:
        if q_item["id"] == item_id:
            q_item["review_message_id"] = message_id
            break
    generator.save_queue(queue)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    products = load_products()
    await update.message.reply_text(
        "Привет 👋\n\n"
        "Какую процедуру делали? Подберу уход по дням.\n\n"
        "<i>📅 А полный календарь восстановления по дням — в закрепе нашего канала "
        "<a href=\"https://t.me/posleprocedur\">@posleprocedur</a></i>",
        reply_markup=procedure_keyboard(products),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def _handle_review(query, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    _, action, item_id = data.split(":", 2)
    queue = generator.load_queue()
    item = next((i for i in queue if i["id"] == item_id), None)

    if not item:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.answer("Пост не найден", show_alert=True)
        return

    if action == "ok":
        try:
            if item.get("type") == "short":
                if CROSSPOST_SHORTS_TO_TG:
                    with open(item["video_path"], "rb") as f:
                        await context.bot.send_video(chat_id=CHANNEL_ID, video=f, supports_streaming=True)
                if youtube_uploader.is_authorized():
                    yt_publish_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=YOUTUBE_DELAY)
                    asyncio.create_task(_upload_to_youtube_bg(context.bot, item, yt_publish_at))
                asyncio.create_task(_upload_to_tiktok_bg(context.bot, item))
            else:
                await context.bot.send_message(chat_id=CHANNEL_ID, text=item["text"])

            item["status"] = "published"
            generator.save_queue(queue)
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer("✅ Опубликован в канал")
        except Exception as e:
            await query.answer(f"Ошибка: {e}", show_alert=True)

    elif action == "sched":
        try:
            slot       = _next_schedule_slot()
            slot_local = slot + datetime.timedelta(hours=3)  # МСК = UTC+3
            item["status"]       = "scheduled"
            item["scheduled_at"] = slot.strftime("%Y-%m-%dT%H:%M:%S")
            generator.save_queue(queue)
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer(f"📅 {slot_local.strftime('%d.%m %H:%M')} МСК")
            slot_str = slot_local.strftime('%d.%m в %H:%M')
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"📅 Запланирован на {slot_str} МСК"
            )
            # Upload to YouTube NOW with publish_at = slot → visible in Studio immediately
            if item.get("type") == "short" and youtube_uploader.is_authorized():
                asyncio.create_task(_upload_to_youtube_bg(context.bot, item, publish_at=slot))
        except Exception as e:
            await query.answer(f"Ошибка: {e}", show_alert=True)

    elif action == "no":
        item["status"] = "rejected"
        generator.save_queue(queue)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.answer("❌ Пропущен")

    elif action == "re":
        item["status"] = "rejected"
        generator.save_queue(queue)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.answer("🔄 Генерирую заново...")
        label = "шорт" if item.get("type") == "short" else "пост"
        status_msg = await context.bot.send_message(chat_id=OWNER_ID, text=f"⏳ Генерирую {label}...")
        stop   = asyncio.Event()
        ticker = asyncio.create_task(_generating_ticker(status_msg, label, stop))
        try:
            if item.get("type") == "short":
                new_item = await asyncio.to_thread(
                    shorts_generator.generate_short, item["procedure"], item.get("template")
                )
            else:
                new_item = await asyncio.to_thread(
                    generator.generate_post, item["procedure"], item.get("post_type")
                )
            stop.set(); ticker.cancel()
            await status_msg.delete()
            await send_review(context.bot, new_item)
        except Exception as e:
            stop.set(); ticker.cancel()
            await status_msg.edit_text(f"Ошибка генерации: {e}")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("rv:"):
        if OWNER_ID and update.effective_user.id == OWNER_ID:
            await _handle_review(query, context, data)
        return

    if data.startswith("plan_nav:") or data.startswith("plan_new:"):
        if OWNER_ID and update.effective_user.id != OWNER_ID:
            return
        prefix, ym = data.split(":", 1)
        year, month = int(ym[:4]), int(ym[5:7])
        if prefix == "plan_new":
            await query.edit_message_text(f"⏳ Генерирую план на {month:02d}.{year}...", parse_mode="HTML")
            await asyncio.to_thread(content_plan.generate_monthly_plan, year, month)
        text = content_plan.format_plan_overview(year, month)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=_plan_nav_keyboard(year, month))
        return

    products = load_products()

    if data == "back:start":
        await query.edit_message_text(
            "Какую процедуру делали?",
            reply_markup=procedure_keyboard(products),
        )

    elif data.startswith("proc:"):
        proc_code = data.split(":", 1)[1]
        proc_data = products.get(proc_code)
        if not proc_data:
            await query.edit_message_text("Не нашла такую процедуру 🤔")
            return
        await query.edit_message_text(
            f"<b>{proc_data['name']}</b>\n\nВыбери, какой сейчас день:",
            reply_markup=phase_keyboard(proc_code, proc_data),
            parse_mode="HTML",
        )

    elif data.startswith("phase:"):
        _, proc_code, phase = data.split(":", 2)
        proc_data = products.get(proc_code)
        if not proc_data or phase not in proc_data:
            await query.edit_message_text("Что-то пошло не так 🤔")
            return

        phase_data = proc_data[phase]
        text = format_phase_text(proc_data["name"], phase_data)
        keyboard = products_keyboard(proc_code)

        await query.edit_message_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )


async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.channel_post:
        state = load_state()
        state["human_posted_since_bot"] = True
        save_state(state)
        logger.info("Новый пост в канале (не от бота) — флаг поднят")


async def scheduled_promo(context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    if not state.get("human_posted_since_bot", True):
        logger.info("Пропускаю промо-пост — последнее сообщение канала от бота")
        return

    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=PROMO_TEXT)
        state["human_posted_since_bot"] = False
        save_state(state)
        logger.info("Промо-пост опубликован в канал")
    except Exception as e:
        logger.error(f"Ошибка публикации в канал: {e}")


async def cmd_insta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pick top videos for manual Instagram Reels posting.
    /insta [N]   — show top N (default 3) videos not yet sent
    /insta reset — clear 'sent' marks so all videos eligible again
    """
    if update.effective_user.id != OWNER_ID:
        return
    args = context.args or []

    queue = generator.load_queue()
    if args and args[0] == "reset":
        cleared = 0
        for q in queue:
            if q.pop("ig_selected_at", None):
                cleared += 1
        generator.save_queue(queue)
        await update.message.reply_text(f"♻️ Сброшено: {cleared} видео снова доступны.")
        return

    n = int(args[0]) if args and args[0].isdigit() else 3

    # Load analytics
    try:
        analytics = json.loads((Path(__file__).parent / "analytics.json").read_text())
    except Exception:
        await update.message.reply_text("⚠️ Нет analytics.json. Запусти /stats refresh.")
        return

    vids = analytics.get("videos", {})

    # Index queue by youtube video_id
    q_by_vid = {}
    for q in queue:
        url = q.get("yt_url") or ""
        if "shorts/" in url:
            vid_id = url.rstrip("/").split("/")[-1]
            q_by_vid[vid_id] = q

    # Score: only last 14 days, not yet sent, has local video file
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=14)).isoformat()
    scored = []
    for vid_id, v in vids.items():
        if not v.get("is_short"):
            continue
        if (v.get("published_at") or "") < cutoff:
            continue
        q = q_by_vid.get(vid_id)
        if not q or q.get("ig_selected_at"):
            continue
        path = q.get("video_path")
        if not path or not os.path.exists(path):
            continue
        views = v.get("views", 0)
        avd   = v.get("avg_view_pct") or 25  # fallback if no retention data yet
        score = views * (avd / 100)
        scored.append((score, vid_id, v, q))

    scored.sort(reverse=True, key=lambda x: x[0])
    top = scored[:n]

    if not top:
        await update.message.reply_text(
            "📭 Нет доступных видео за 14 дней.\n"
            "Возможно все уже отправлены — /insta reset"
        )
        return

    await update.message.reply_text(
        f"🎬 Топ-{len(top)} для Instagram (по score = views × retention%)\n"
        f"Скачай и залей в Reels вручную."
    )

    for score, vid_id, v, q in top:
        proc = q.get("procedure", "?")
        tmpl = q.get("template", "?")
        views = v.get("views", 0)
        likes = v.get("likes", 0)
        avd = v.get("avg_view_pct", "-")
        title = v.get("title", "")[:80]
        caption = (
            f"<b>{title}</b>\n"
            f"<code>{proc}/{tmpl}</code>\n"
            f"👁 {views:,}  ❤️ {likes}  ⏱ {avd}% AVD\n"
            f"🔗 youtube.com/shorts/{vid_id}"
        )
        try:
            with open(q["video_path"], "rb") as f:
                await context.bot.send_video(
                    chat_id=OWNER_ID,
                    video=f,
                    caption=caption,
                    parse_mode="HTML",
                    supports_streaming=True,
                )
            q["ig_selected_at"] = datetime.datetime.utcnow().isoformat()
        except Exception as e:
            logger.exception("cmd_insta: failed to send %s", vid_id)
            await update.message.reply_text(f"⚠️ {vid_id}: {type(e).__name__}: {e}")

    generator.save_queue(queue)
    await update.message.reply_text(
        f"✅ Отправлено {len(top)}. Эти видео не появятся снова в /insta.\n"
        f"Когда нужно ещё — повтори /insta {n}"
    )


async def keep_ollama_warm(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a minimal request every 20 min so the model stays loaded in RAM."""
    def _ping():
        try:
            requests.post(
                generator.OLLAMA_URL,
                json={"model": generator.MODEL, "messages": [{"role": "user", "content": "1"}],
                      "stream": False, "keep_alive": "24h",
                      "options": {"temperature": 0, "num_predict": 1}},
                timeout=600,
            )
        except Exception as e:
            logger.warning("Ollama warmup failed: %s", e)

    await asyncio.to_thread(_ping)
    logger.info("Ollama keep-alive ping sent")


def _autopublish_on() -> bool:
    """True if autopublish flag file exists (toggle-based, no expiry)."""
    return AUTOPUBLISH_FILE.exists()


async def _auto_publish_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate and immediately publish a short without review (when autopublish is on).
    Smart-skip: проверяем условия (свежие комбо + recent AVD). Если плохо — пропускаем слот.
    """
    if not _autopublish_on():
        return
    skip, reason = await asyncio.to_thread(shorts_generator.should_skip_slot)
    if skip:
        logger.info("Auto-publish slot skipped: %s", reason)
        return
    try:
        item = await asyncio.to_thread(shorts_generator.generate_short, None, None)
        title = _short_title(item)
        if CROSSPOST_SHORTS_TO_TG:
            with open(item["video_path"], "rb") as f:
                await context.bot.send_video(chat_id=CHANNEL_ID, video=f, supports_streaming=True)
        queue = generator.load_queue()
        for q in queue:
            if q["id"] == item["id"]:
                q["status"] = "published"
                break
        generator.save_queue(queue)
        await context.bot.send_message(chat_id=OWNER_ID, text=f"🤖 Авто: «{title[:80]}»")
        if youtube_uploader.is_authorized():
            asyncio.create_task(_upload_to_youtube_bg(context.bot, item))
        asyncio.create_task(_upload_to_tiktok_bg(context.bot, item))
    except Exception as e:
        logger.exception("_auto_publish_job failed")
        if OWNER_ID:
            try:
                await context.bot.send_message(chat_id=OWNER_ID, text=f"⚠️ Авто-публикация: {type(e).__name__}: {e}")
            except Exception:
                pass


async def cmd_autopublish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle autopublish on/off. No expiry — runs until you turn it off."""
    if update.effective_user.id != OWNER_ID:
        return
    if _autopublish_on():
        AUTOPUBLISH_FILE.unlink(missing_ok=True)
        await update.message.reply_text("🔴 Авто-публикация выключена")
    else:
        AUTOPUBLISH_FILE.write_text("on")
        await update.message.reply_text(
            "🟢 Авто-публикация включена\n"
            "Слоты: 08:00, 11:00, 14:00, 17:00, 20:00 МСК\n\n"
            "/autopublish — выключить"
        )


async def daily_generate(context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Запуск автогенерации поста")
    try:
        item = await asyncio.to_thread(generator.generate_post)
        await send_review(context.bot, item)
    except Exception as e:
        logger.error(f"Ошибка автогенерации: {e}")
        if OWNER_ID:
            try:
                await context.bot.send_message(chat_id=OWNER_ID, text=f"⚠️ Ошибка генерации: {e}")
            except Exception:
                pass


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    queue = generator.load_queue()
    pending = [i for i in queue if i["status"] == "pending"]
    if not pending:
        await update.message.reply_text("Очередь пуста.")
        return
    lines = [f"В очереди: {len(pending)}\n"]
    for item in pending[-10:]:
        proc = generator.PROCEDURE_NAMES.get(item["procedure"], item["procedure"])
        lines.append(f"• [{item['id']}] {item['post_type']} / {proc}")
    await update.message.reply_text("\n".join(lines))


async def _generating_ticker(msg, label: str, stop: asyncio.Event) -> None:
    """Edit message every 30s with elapsed time so user knows it's still working."""
    t0 = asyncio.get_event_loop().time()
    while not stop.is_set():
        await asyncio.sleep(30)
        if stop.is_set():
            break
        elapsed = int(asyncio.get_event_loop().time() - t0)
        try:
            await msg.edit_text(f"⏳ Генерирую {label}... {elapsed}с\n(Ollama загружает модель)")
        except Exception:
            pass


async def publish_scheduled(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Publish to Telegram+TikTok when scheduled_at arrives (YouTube already uploaded on scheduling)."""
    now   = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    queue = generator.load_queue()
    to_publish = [
        i for i in queue
        if i.get("status") == "scheduled" and i.get("scheduled_at", "9999") <= now
    ]
    for item in to_publish:
        try:
            if item.get("type") == "short":
                if CROSSPOST_SHORTS_TO_TG:
                    with open(item["video_path"], "rb") as f:
                        await context.bot.send_video(chat_id=CHANNEL_ID, video=f, supports_streaming=True)
                asyncio.create_task(_upload_to_tiktok_bg(context.bot, item))
            else:
                await context.bot.send_message(chat_id=CHANNEL_ID, text=item["text"])
            item["status"] = "published"
            logger.info("Scheduled item published: %s", item["id"])
        except Exception as e:
            logger.error("Failed to publish scheduled %s: %s", item["id"], e)
    if to_publish:
        generator.save_queue(queue)


async def cmd_series(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    series_pool = list(shorts_generator.SERIES)
    if not context.args:
        await update.message.reply_text(
            f"Использование: /series [название]\nДоступные: {', '.join(series_pool)}"
        )
        return
    key = context.args[0].lower()
    if key not in shorts_generator.SERIES:
        await update.message.reply_text(
            f"Серия «{key}» не найдена.\nДоступные: {', '.join(series_pool)}"
        )
        return
    msg  = await update.message.reply_text(f"⏳ Генерирую серию «{key}» (3 ролика)...")
    stop = asyncio.Event()
    ticker = asyncio.create_task(_generating_ticker(msg, f"серию «{key}»", stop))
    try:
        items = await asyncio.to_thread(shorts_generator.generate_series, key)
        stop.set(); ticker.cancel()
        await msg.delete()
        for item in items:
            await send_review(context.bot, item)
    except Exception as e:
        stop.set(); ticker.cancel()
        await msg.edit_text(f"Ошибка: {e}")


async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    today = datetime.date.today()
    year, month = today.year, today.month
    if context.args and context.args[0] == "new":
        msg = await update.message.reply_text("⏳ Генерирую план...")
        await asyncio.to_thread(content_plan.generate_monthly_plan, year, month)
        text = content_plan.format_plan_overview(year, month)
        await msg.edit_text(text, parse_mode="HTML", reply_markup=_plan_nav_keyboard(year, month))
    else:
        text = content_plan.format_plan_overview(year, month)
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=_plan_nav_keyboard(year, month))


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    text = content_plan.format_today_tasks()
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_today_gen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    tasks = content_plan.today_tasks()
    if not tasks:
        await update.message.reply_text("Нет плана на сегодня. Используй /plan new")
        return

    today_str      = datetime.date.today().isoformat()
    pending_shorts = [(i, s) for i, s in enumerate(tasks["shorts"]) if s["status"] == "pending"]
    pending_post   = tasks["post"] if tasks["post"]["status"] == "pending" else None
    total = len(pending_shorts) + (1 if pending_post else 0)

    if total == 0:
        await update.message.reply_text("На сегодня всё уже сгенерировано!")
        return

    msg = await update.message.reply_text(f"⏳ Генерирую {total} материала...")

    for i, short in pending_shorts:
        stop   = asyncio.Event()
        ticker = asyncio.create_task(_generating_ticker(msg, f"шорт {i + 1}", stop))
        try:
            item = await asyncio.to_thread(
                shorts_generator.generate_short, short["procedure"], short["template"]
            )
            stop.set(); ticker.cancel()
            content_plan.mark_item(today_str, "short", i, item["id"], "generated")
            await send_review(context.bot, item)
        except Exception as e:
            stop.set(); ticker.cancel()
            logger.exception("Ошибка генерации шорт %d", i + 1)
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"⚠️ Ошибка шорт {i + 1} ({type(e).__name__}): {e}"
            )

    if pending_post:
        p      = pending_post
        stop   = asyncio.Event()
        ticker = asyncio.create_task(_generating_ticker(msg, "пост", stop))
        try:
            item = await asyncio.to_thread(
                generator.generate_post, p["procedure"], p["post_type"]
            )
            stop.set(); ticker.cancel()
            content_plan.mark_item(today_str, "post", None, item["id"], "generated")
            await send_review(context.bot, item)
        except Exception as e:
            stop.set(); ticker.cancel()
            logger.exception("Ошибка генерации поста")
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"⚠️ Ошибка поста ({type(e).__name__}): {e}"
            )

    try:
        await msg.delete()
    except Exception:
        pass


async def weekly_analytics(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Auto-refresh own channel stats every week and notify owner."""
    import json as _json
    try:
        # Skip if analytics were refreshed less than 6 days ago
        analytics_file = Path(__file__).parent / "analytics.json"
        if analytics_file.exists():
            updated_at = _json.loads(analytics_file.read_text()).get("updated_at")
            if updated_at:
                age = datetime.datetime.utcnow() - datetime.datetime.fromisoformat(updated_at)
                if age.total_seconds() < 6 * 24 * 3600:
                    logger.info("weekly_analytics: skipped, last refresh %s ago", age)
                    return
        await asyncio.to_thread(yt_stats.refresh)
        if OWNER_ID:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text="📊 Еженедельная аналитика:\n\n" + yt_stats.format_stats(),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            insights = await asyncio.to_thread(yt_stats.weekly_insights)
            if insights:
                await context.bot.send_message(
                    chat_id=OWNER_ID,
                    text="🧠 <b>Insights недели:</b>\n\n" + insights,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
        logger.info("Weekly analytics updated")
    except Exception as e:
        logger.exception("weekly_analytics failed: %s", e)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    refresh = context.args and context.args[0] == "refresh"
    if refresh:
        msg = await update.message.reply_text("⏳ Обновляю статистику с YouTube...")
        try:
            await asyncio.to_thread(yt_stats.refresh)
            await msg.delete()
        except Exception as e:
            logger.exception("yt_stats refresh failed")
            await msg.edit_text(f"⚠️ Ошибка: {e}")
            return
    await update.message.reply_text(yt_stats.format_stats(), parse_mode="HTML",
                                    disable_web_page_preview=True)
    if refresh:
        msg2 = await update.message.reply_text("⏳ Генерирую insights...")
        insights = await asyncio.to_thread(yt_stats.weekly_insights)
        await msg2.delete()
        if insights:
            await update.message.reply_text("🧠 <b>Insights недели:</b>\n\n" + insights,
                                            parse_mode="HTML", disable_web_page_preview=True)


async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    args = context.args or []

    if not args or args[0] == "show":
        text = yt_research.format_research_summary()
        await update.message.reply_text(text, parse_mode="HTML",
                                        disable_web_page_preview=True)
        return

    # /research run [ru|en|ko|fr] [--transcribe]
    if args[0] == "run":
        langs       = [a for a in args[1:] if a in ("ru", "en", "ko", "fr")] or None
        transcribe  = "--transcribe" in args
        lang_str    = ", ".join(langs) if langs else "все языки"
        t_str       = " + транскрипция" if transcribe else " (только заголовки)"
        msg = await update.message.reply_text(
            f"🔍 Запускаю исследование: {lang_str}{t_str}…\n"
            f"{'⚠️ Транскрипция займёт 20–40 мин.' if transcribe else 'Без транскрипции — ~5 мин.'}"
        )
        stop   = asyncio.Event()
        ticker = asyncio.create_task(_generating_ticker(msg, "исследование", stop))
        try:
            await asyncio.to_thread(yt_research.run_research,
                                    languages=langs, transcribe=transcribe)
            stop.set(); ticker.cancel()
            await msg.delete()
            text = yt_research.format_research_summary()
            await update.message.reply_text(text, parse_mode="HTML",
                                            disable_web_page_preview=True)
        except Exception as e:
            stop.set(); ticker.cancel()
            logger.exception("yt_research failed")
            await msg.edit_text(f"⚠️ Ошибка исследования: {e}")
        return

    await update.message.reply_text(
        "Использование:\n"
        "/research — показать результаты\n"
        "/research run — запустить (RU + EN + KO + FR, только заголовки)\n"
        "/research run en — только английский\n"
        "/research run en --transcribe — с транскрипцией и анализом структуры"
    )


async def cmd_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("cmd_generate called: user_id=%s owner_id=%s args=%s",
                update.effective_user.id if update.effective_user else "none", OWNER_ID, context.args)
    if update.effective_user.id != OWNER_ID:
        logger.warning("cmd_generate: access denied (user %s != owner %s)",
                       update.effective_user.id, OWNER_ID)
        return
    args = context.args or []
    content_type = args[0] if args else "post"

    if content_type not in ("post", "short"):
        await update.message.reply_text(
            "Использование: /generate [post|short] [процедура] [тип]\n"
            f"Процедуры: {', '.join(generator.PROCEDURES)}\n"
            f"Типы постов: {', '.join(generator.POST_TYPES)}\n"
            f"Типы шортов: {', '.join(list(shorts_generator.TEMPLATES) + list(shorts_generator.GENERAL_TEMPLATES))}"
        )
        return

    # Each extra arg is classified as procedure or content-type — order doesn't matter
    procedure = None
    post_type = None
    template  = None
    short_pool = set(shorts_generator.TEMPLATES) | set(shorts_generator.GENERAL_TEMPLATES)

    for arg in args[1:]:
        if arg in generator.PROCEDURES:
            procedure = arg
        elif content_type == "post" and arg in generator.POST_TYPES:
            post_type = arg
        elif content_type == "short" and arg in short_pool:
            template = arg
        else:
            await update.message.reply_text(
                f"Неизвестный аргумент «{arg}».\n"
                f"Процедуры: {', '.join(generator.PROCEDURES)}\n"
                f"Типы постов: {', '.join(generator.POST_TYPES)}\n"
                f"Типы шортов: {', '.join(sorted(short_pool))}"
            )
            return

    label = "пост" if content_type == "post" else "шорт"
    msg  = await update.message.reply_text(f"⏳ Генерирую {label}...")
    stop = asyncio.Event()
    ticker = asyncio.create_task(_generating_ticker(msg, label, stop))
    try:
        if content_type == "short":
            item = await asyncio.to_thread(shorts_generator.generate_short, procedure, template)
        else:
            item = await asyncio.to_thread(generator.generate_post, procedure, post_type)
        stop.set(); ticker.cancel()
        await msg.delete()
        await send_review(context.bot, item)
    except Exception as e:
        stop.set(); ticker.cancel()
        await msg.edit_text(f"Ошибка: {e}")


async def cmd_post_magnets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate (if needed) and post all 7 lead-magnet calendars to the channel.
    Pin each in TG manually afterwards (Telegram bots have limited pin rights)."""
    if update.effective_user.id != OWNER_ID:
        return
    args = context.args or []
    procedure_filter = args[0] if args else None

    msg = await update.message.reply_text("⏳ Готовлю lead-magnets (это долго, до 10 минут)...")
    try:
        if procedure_filter and procedure_filter in lead_magnets.PROCEDURES:
            await asyncio.to_thread(lead_magnets.regenerate, procedure_filter)
        else:
            await asyncio.to_thread(lead_magnets.generate_all)

        data = lead_magnets.load()
        items = {k: v for k, v in data.items() if not procedure_filter or k == procedure_filter}

        await msg.edit_text(f"✅ Готово, {len(items)} магнитов. Публикую в канал...")
        posted = []
        for proc_id, m in items.items():
            try:
                sent = await context.bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=m["text"],
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                posted.append((proc_id, sent.message_id))
                # Try to pin (works if bot is admin with pin rights)
                try:
                    await context.bot.pin_chat_message(
                        chat_id=CHANNEL_ID,
                        message_id=sent.message_id,
                        disable_notification=True,
                    )
                except Exception as pin_err:
                    logger.info("Pin failed for %s: %s", proc_id, pin_err)
            except Exception as e:
                logger.exception("Failed to post magnet %s", proc_id)
                await update.message.reply_text(f"⚠️ {proc_id}: {e}")

        await update.message.reply_text(
            f"📌 Опубликовано {len(posted)} магнитов в @posleprocedur. "
            f"Если автоматический pin не сработал — закрепи каждый вручную."
        )
    except Exception as e:
        logger.exception("cmd_post_magnets failed")
        await msg.edit_text(f"⚠️ Ошибка: {type(e).__name__}: {e}")


async def cmd_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate next non-repeating short (unique procedure+template combo)."""
    if update.effective_user.id != OWNER_ID:
        return
    label = "шорт"
    msg   = await update.message.reply_text("⏳ Генерирую следующий уникальный шорт...")
    stop  = asyncio.Event()
    ticker = asyncio.create_task(_generating_ticker(msg, label, stop))
    try:
        item = await asyncio.to_thread(shorts_generator.generate_short, None, None)
        stop.set(); ticker.cancel()
        await msg.delete()
        await send_review(context.bot, item)
    except Exception as e:
        stop.set(); ticker.cancel()
        logger.exception("cmd_next failed")
        await msg.edit_text(f"⚠️ Ошибка: {type(e).__name__}: {e}")


def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN не задан. Добавь в .env или переменные среды.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("queue",     cmd_queue))
    app.add_handler(CommandHandler("generate",  cmd_generate))
    app.add_handler(CommandHandler("series",    cmd_series))
    app.add_handler(CommandHandler("plan",      cmd_plan))
    app.add_handler(CommandHandler("today",     cmd_today))
    app.add_handler(CommandHandler("today_gen", cmd_today_gen))
    app.add_handler(CommandHandler("stats",        cmd_stats))
    app.add_handler(CommandHandler("research",     cmd_research))
    app.add_handler(CommandHandler("next",         cmd_next))
    app.add_handler(CommandHandler("autopublish",  cmd_autopublish))
    app.add_handler(CommandHandler("post_magnets", cmd_post_magnets))
    app.add_handler(CommandHandler("insta",        cmd_insta))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, on_channel_post))

    app.job_queue.run_repeating(scheduled_promo,   interval=POST_INTERVAL, first=60)
    app.job_queue.run_repeating(keep_ollama_warm,  interval=20 * 60,       first=30)
    app.job_queue.run_repeating(publish_scheduled, interval=30 * 60,       first=60)
    app.job_queue.run_repeating(weekly_analytics,  interval=7 * 24 * 3600, first=3600)
    # Ежедневная генерация поста в 09:00 МСК (06:00 UTC)
    app.job_queue.run_daily(
        daily_generate,
        time=datetime.time(6, 0, tzinfo=datetime.timezone.utc),
    )
    # Авто-публикация шортов: 5 слотов (08, 11, 14, 17, 20 МСК = 05, 08, 11, 14, 17 UTC)
    for _h in (5, 8, 11, 14, 17):
        app.job_queue.run_daily(
            _auto_publish_job,
            time=datetime.time(_h, 0, tzinfo=datetime.timezone.utc),
        )

    logger.info("Бот запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
