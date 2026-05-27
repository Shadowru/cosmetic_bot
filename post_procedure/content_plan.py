import calendar
import datetime
import json
import random
from pathlib import Path

import generator as g
import shorts_generator as sg

BASE_DIR  = Path(__file__).parent
PLAN_FILE = BASE_DIR / "content_plan.json"

SHORTS_PER_DAY = 2
# Slot times in UTC (09:00 МСК = 06:00 UTC, 19:00 МСК = 16:00 UTC)
SHORT_SLOTS_UTC = ["06:00", "16:00"]
POST_SLOT_UTC   = "08:00"

PROCEDURES = [p for p in sg.PROCEDURES if p != "general"]
TEMPLATES  = [t for t in sg.TEMPLATES if t not in sg.TEMPLATE_BLACKLIST]
GEN_TMPLS  = [t for t in sg.GENERAL_TEMPLATES if t not in sg.TEMPLATE_BLACKLIST]
POST_TYPES = g.POST_TYPES


def load_plan() -> dict:
    if PLAN_FILE.exists():
        return json.loads(PLAN_FILE.read_text(encoding="utf-8"))
    return {}


def save_plan(plan: dict) -> None:
    PLAN_FILE.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")


def _month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def generate_monthly_plan(year: int, month: int) -> dict:
    """Generate a full monthly plan: 2 shorts + 1 post per day."""
    plan       = load_plan()
    month_key  = _month_key(year, month)
    days       = calendar.monthrange(year, month)[1]

    rng        = random.Random(year * 100 + month)
    proc_pool  = PROCEDURES * 4          # enough for rotation
    rng.shuffle(proc_pool)
    proc_iter  = iter(proc_pool)

    # Track recent to avoid repetition
    recent_procs: list[str] = []
    recent_tmpls: dict[str, list[str]] = {p: [] for p in PROCEDURES}

    def next_proc() -> str:
        for _ in range(20):
            p = next(proc_iter, None)
            if p is None:
                pool = PROCEDURES[:]
                rng.shuffle(pool)
                for pp in pool:
                    if pp not in recent_procs[-3:]:
                        recent_procs.append(pp)
                        return pp
                return rng.choice(PROCEDURES)
            if p not in recent_procs[-3:]:
                recent_procs.append(p)
                return p
        return rng.choice(PROCEDURES)

    # Load performance weights from yt_stats (best templates get higher probability)
    try:
        import yt_stats as _ys
        _weights = _ys.get_template_weights()
    except Exception:
        _weights = {}

    def next_tpl(proc: str) -> str:
        used = recent_tmpls.get(proc, [])
        unused = [t for t in TEMPLATES if t not in used]
        pool  = unused if unused else TEMPLATES
        if _weights:
            weights = [_weights.get(t, 1.0) for t in pool]
            tpl = rng.choices(pool, weights=weights, k=1)[0]
        else:
            tpl = rng.choice(pool)
        recent_tmpls.setdefault(proc, []).append(tpl)
        if len(recent_tmpls[proc]) > len(TEMPLATES):
            recent_tmpls[proc] = []
        return tpl

    month_plan: dict[str, dict] = {}
    for day in range(1, days + 1):
        date_str = f"{year:04d}-{month:02d}-{day:02d}"

        shorts = []
        for slot_idx in range(SHORTS_PER_DAY):
            # Every 5th slot → general topic
            total_slot = (day - 1) * SHORTS_PER_DAY + slot_idx
            if total_slot % 5 == 4:
                proc = "general"
                tpl  = GEN_TMPLS[total_slot % len(GEN_TMPLS)]
            else:
                proc = next_proc()
                tpl  = next_tpl(proc)
            shorts.append({
                "procedure": proc,
                "template":  tpl,
                "slot":      SHORT_SLOTS_UTC[slot_idx],
                "status":    "pending",
                "queue_id":  None,
            })

        post_proc = PROCEDURES[(day - 1) % len(PROCEDURES)]
        post_type = POST_TYPES[(day - 1) % len(POST_TYPES)]
        post = {
            "procedure": post_proc,
            "post_type": post_type,
            "slot":      POST_SLOT_UTC,
            "status":    "pending",
            "queue_id":  None,
        }

        month_plan[date_str] = {"shorts": shorts, "post": post}

    plan[month_key] = month_plan
    save_plan(plan)
    return month_plan


def get_month_plan(year: int, month: int) -> dict:
    plan = load_plan()
    return plan.get(_month_key(year, month), {})


def today_tasks() -> dict:
    today = datetime.date.today()
    plan  = load_plan()
    key   = _month_key(today.year, today.month)
    return plan.get(key, {}).get(today.isoformat(), {})


def mark_item(date_str: str, item_type: str, idx: int | None, queue_id: str, status: str = "generated") -> None:
    """Update status of a plan item after generation/publishing."""
    plan      = load_plan()
    today     = datetime.date.today()
    month_key = _month_key(today.year, today.month)
    day_plan  = plan.get(month_key, {}).get(date_str)
    if not day_plan:
        return
    if item_type == "short" and idx is not None:
        if 0 <= idx < len(day_plan["shorts"]):
            day_plan["shorts"][idx]["status"]   = status
            day_plan["shorts"][idx]["queue_id"] = queue_id
    elif item_type == "post":
        day_plan["post"]["status"]   = status
        day_plan["post"]["queue_id"] = queue_id
    save_plan(plan)


def format_plan_overview(year: int, month: int) -> str:
    month_plan = get_month_plan(year, month)
    if not month_plan:
        return f"План на {month:02d}.{year} не создан. Используй /plan new"

    month_names = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                   "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
    day_names   = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    STATUS_ICON = {"pending": "⬜", "generated": "🟡", "published": "✅", "scheduled": "📅"}

    lines = [f"📅 <b>{month_names[month]} {year}</b>\n"]
    today = datetime.date.today().isoformat()

    for date_str, day in sorted(month_plan.items()):
        d    = datetime.date.fromisoformat(date_str)
        dow  = day_names[d.weekday()]
        day_num = d.day
        marker = "▶" if date_str == today else " "

        s_icons = "".join(STATUS_ICON.get(s["status"], "⬜") for s in day["shorts"])
        p_icon  = STATUS_ICON.get(day["post"]["status"], "⬜")

        # Short labels: proc/tpl abbreviated
        s_labels = "  ".join(
            f"{s['procedure'][:4]}/{s['template'][:4]}" for s in day["shorts"]
        )
        lines.append(f"{marker}<code>{day_num:2d} {dow}</code>  {s_icons}{p_icon}  {s_labels}")

    lines.append("\n⬜ не сгенерировано  🟡 в очереди  📅 запланировано  ✅ опубликовано")
    return "\n".join(lines)


def format_today_tasks() -> str:
    tasks = today_tasks()
    if not tasks:
        today = datetime.date.today()
        return f"Нет плана на {today.strftime('%d.%m')}. Используй /plan new"

    STATUS_ICON = {"pending": "⬜", "generated": "🟡", "published": "✅", "scheduled": "📅"}
    today_str = datetime.date.today().strftime("%d.%m")
    lines = [f"<b>📍 {today_str} — план дня</b>\n"]

    for i, s in enumerate(tasks["shorts"], 1):
        icon = STATUS_ICON.get(s["status"], "⬜")
        proc_name = sg.PROC_NAMES_NOM.get(s["procedure"], s["procedure"])
        all_tpl   = {**sg.TEMPLATES, **sg.GENERAL_TEMPLATES}
        tpl_title = all_tpl.get(s["template"], s["template"])
        tpl_short = tpl_title.format(proc=proc_name)[:60]
        lines.append(f"{icon} <b>Шорт {i}</b> ({s['slot']} UTC)\n   {tpl_short}")

    p = tasks["post"]
    icon = STATUS_ICON.get(p["status"], "⬜")
    proc_name = g.PROCEDURE_NAMES.get(p["procedure"], p["procedure"])
    lines.append(f"\n{icon} <b>Пост</b> ({p['slot']} UTC)\n   {p['post_type']} / {proc_name}")

    pending_shorts = [i for i, s in enumerate(tasks["shorts"]) if s["status"] == "pending"]
    pending_post   = p["status"] == "pending"
    if pending_shorts or pending_post:
        lines.append("\n/today_gen — сгенерировать всё за сегодня")

    return "\n".join(lines)
