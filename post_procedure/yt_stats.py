"""
Own channel analytics: fetch video stats + retention, track template/procedure performance,
generate weekly AI insights via Ollama.

Run standalone:  python3 yt_stats.py
Bot:             import yt_stats; yt_stats.refresh()
"""
import json
import logging
import datetime
import os
import requests
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR       = Path(__file__).parent
ANALYTICS_FILE = BASE_DIR / "analytics.json"
QUEUE_FILE     = BASE_DIR / "content_queue.json"

from config import OLLAMA_URL, OLLAMA_MODEL as MODEL, OLLAMA_TIMEOUT, atomic_write_json

SCOPES = [
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


# ─── YouTube clients ─────────────────────────────────────────────────────────

def _creds():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    token_file = BASE_DIR / "yt_token.json"
    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json())
    return creds


def _yt():
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=_creds(), cache_discovery=False)


def _yta():
    """YouTube Analytics API v2 client — for retention, CTR, watch time."""
    from googleapiclient.discovery import build
    return build("youtubeAnalytics", "v2", credentials=_creds(), cache_discovery=False)


# ─── Data helpers ────────────────────────────────────────────────────────────

def _load() -> dict:
    if ANALYTICS_FILE.exists():
        return json.loads(ANALYTICS_FILE.read_text(encoding="utf-8"))
    return {"updated_at": None, "channel": {}, "videos": {}, "template_stats": {}, "proc_stats": {}}


def _save(data: dict):
    data["updated_at"] = datetime.datetime.utcnow().isoformat()
    atomic_write_json(ANALYTICS_FILE, data)


def _queue_index() -> dict:
    """Map YouTube video ID → queue item (for template/procedure lookup)."""
    if not QUEUE_FILE.exists():
        return {}
    queue = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    idx = {}
    for item in queue:
        url = item.get("yt_url") or ""
        if "youtube.com/shorts/" in url or "youtu.be/" in url:
            vid_id = url.rstrip("/").split("/")[-1]
            idx[vid_id] = item
    return idx


# ─── Data API fetch ──────────────────────────────────────────────────────────

def _get_channel_id(yt):
    resp = yt.channels().list(part="id,statistics,snippet", mine=True).execute()
    ch = resp["items"][0]
    return ch["id"], ch["snippet"]["title"], ch["statistics"]


def _get_uploads_playlist(yt, channel_id: str) -> str:
    resp = yt.channels().list(part="contentDetails", id=channel_id).execute()
    return resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]


def _get_all_videos(yt, playlist_id: str) -> list:
    videos, page_token = [], None
    while True:
        kwargs = dict(part="snippet", playlistId=playlist_id, maxResults=50)
        if page_token:
            kwargs["pageToken"] = page_token
        resp = yt.playlistItems().list(**kwargs).execute()
        for item in resp.get("items", []):
            videos.append({
                "id":           item["snippet"]["resourceId"]["videoId"],
                "title":        item["snippet"]["title"],
                "published_at": item["snippet"]["publishedAt"],
            })
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return videos


def _get_video_stats(yt, video_ids: list) -> dict:
    stats = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        resp = yt.videos().list(
            part="statistics,contentDetails",
            id=",".join(batch)
        ).execute()
        for item in resp.get("items", []):
            s = item["statistics"]
            dur = item["contentDetails"]["duration"]
            stats[item["id"]] = {
                "views":    int(s.get("viewCount", 0)),
                "likes":    int(s.get("likeCount", 0)),
                "comments": int(s.get("commentCount", 0)),
                "duration": dur,
                "is_short": _is_short(dur),
            }
    return stats


def _is_short(iso_duration: str) -> bool:
    import re
    m = re.match(r"PT(?:(\d+)M)?(?:(\d+)S)?", iso_duration)
    if not m:
        return False
    minutes = int(m.group(1) or 0)
    seconds = int(m.group(2) or 0)
    return minutes * 60 + seconds <= 90


# ─── Analytics API fetch ─────────────────────────────────────────────────────

def _get_retention_data(channel_id: str, days: int = 30) -> dict:
    """
    Fetch averageViewPercentage + views per video via YouTube Analytics API.
    Returns {video_id: {avg_view_pct, views_analytics, likes_analytics}}.
    """
    try:
        yta = _yta()
        end   = datetime.date.today()
        start = end - datetime.timedelta(days=days)
        resp  = yta.reports().query(
            ids=f"channel=={channel_id}",
            startDate=start.isoformat(),
            endDate=end.isoformat(),
            metrics="views,averageViewPercentage,likes,comments,estimatedMinutesWatched",
            dimensions="video",
            sort="-views",
            maxResults=200,
        ).execute()
        result = {}
        col_names = [h["name"] for h in resp.get("columnHeaders", [])]
        for row in resp.get("rows", []):
            row_dict = dict(zip(col_names, row))
            vid_id   = row_dict.get("video", "")
            result[vid_id] = {
                "avg_view_pct":   round(row_dict.get("averageViewPercentage", 0), 1),
                "views_analytics": int(row_dict.get("views", 0)),
                "likes_analytics": int(row_dict.get("likes", 0)),
                "comments_analytics": int(row_dict.get("comments", 0)),
                "watch_minutes":  round(row_dict.get("estimatedMinutesWatched", 0), 1),
            }
        return result
    except Exception as e:
        logger.warning("Analytics API unavailable: %s", e)
        return {}


def _get_retention_bucket(channel_id: str, video_id: str, ratio_start: float,
                          ratio_end: float = 1.0, days: int = 30) -> float | None:
    """Avg audienceWatchRatio в окне [ratio_start, ratio_end] elapsedVideoTimeRatio.

    Используется для двух метрик: CTA-reach (последние 5 сек ~ 0.83-1.0) и
    swipe-survival (первые 3 сек ~ 0.0-0.1). Возвращает % или None.
    """
    try:
        yta = _yta()
        end   = datetime.date.today()
        start = end - datetime.timedelta(days=days)
        resp  = yta.reports().query(
            ids=f"channel=={channel_id}",
            startDate=start.isoformat(),
            endDate=end.isoformat(),
            metrics="audienceWatchRatio",
            dimensions="elapsedVideoTimeRatio",
            filters=f"video=={video_id}",
        ).execute()
        rows = resp.get("rows", [])
        if not rows:
            return None
        bucket = [r[1] for r in rows if ratio_start <= r[0] <= ratio_end]
        if not bucket:
            return None
        return round(sum(bucket) / len(bucket) * 100, 1)
    except Exception as e:
        logger.warning("retention bucket API unavailable for %s: %s", video_id, e)
        return None


def _get_cta_retention(channel_id: str, video_id: str, days: int = 30) -> float | None:
    """% зрителей доживших до CTA-карточки (~последние 5 сек 30-сек шорта)."""
    return _get_retention_bucket(channel_id, video_id, 0.83, 1.0, days)


def _get_swipe_survival(channel_id: str, video_id: str, days: int = 30) -> float | None:
    """% зрителей оставшихся после первых ~3 сек (защита KICKER от swipe-away)."""
    return _get_retention_bucket(channel_id, video_id, 0.0, 0.1, days)


# ─── Aggregation ─────────────────────────────────────────────────────────────

def _aggregate(videos: list, stats: dict, queue_idx: dict) -> tuple[dict, dict]:
    # Сначала собираем raw observations с привязкой к (template, procedure),
    # чтобы потом нормализовать template-метрику по средней процедуры.
    # Иначе days_7/botox (botox — топовая процедура) выглядит «победителем шаблона»,
    # хотя это эффект процедуры. lift = views / proc_avg показывает реальный вклад шаблона.
    obs: list[tuple[str, str, int]] = []  # (template, procedure, views)
    for v in videos:
        vid_id = v["id"]
        if vid_id not in stats or not stats[vid_id]["is_short"]:
            continue
        item = queue_idx.get(vid_id)
        if not item:
            continue
        t = item.get("template")
        p = item.get("procedure")
        if t and p:
            obs.append((t, p, stats[vid_id]["views"]))

    proc: dict[str, list[int]] = {}
    for _, p, views in obs:
        proc.setdefault(p, []).append(views)
    proc_avg = {p: sum(vs) / len(vs) for p, vs in proc.items() if vs}

    tmpl_views: dict[str, list[int]]   = {}
    tmpl_lifts: dict[str, list[float]] = {}
    for t, p, views in obs:
        tmpl_views.setdefault(t, []).append(views)
        if proc_avg.get(p, 0):
            tmpl_lifts.setdefault(t, []).append(views / proc_avg[p])

    def summarise_simple(d):
        return {
            k: {"count": len(v), "avg": round(sum(v) / len(v)),
                "max": max(v), "total": sum(v)}
            for k, v in d.items() if v
        }

    tmpl_summary = summarise_simple(tmpl_views)
    for t, lifts in tmpl_lifts.items():
        if t in tmpl_summary and lifts:
            tmpl_summary[t]["avg_lift"] = round(sum(lifts) / len(lifts), 2)

    return tmpl_summary, summarise_simple(proc)


# ─── Main refresh ────────────────────────────────────────────────────────────

def refresh() -> dict:
    """Pull fresh stats from YouTube Data + Analytics APIs and save to analytics.json."""
    yt   = _yt()
    data = _load()

    channel_id, ch_title, ch_stats = _get_channel_id(yt)
    data["channel"] = {
        "id":          channel_id,
        "title":       ch_title,
        "subscribers": int(ch_stats.get("subscriberCount", 0)),
        "total_views": int(ch_stats.get("viewCount", 0)),
        "video_count": int(ch_stats.get("videoCount", 0)),
    }

    playlist_id = _get_uploads_playlist(yt, channel_id)
    videos      = _get_all_videos(yt, playlist_id)
    video_ids   = [v["id"] for v in videos]
    stats       = _get_video_stats(yt, video_ids)

    # Merge Data API stats
    for v in videos:
        vid_id   = v["id"]
        existing = data["videos"].get(vid_id, {})
        data["videos"][vid_id] = {**existing, **v, **(stats.get(vid_id, {}))}

    # Merge Analytics API retention data (last 30 days)
    retention = _get_retention_data(channel_id, days=30)
    for vid_id, r in retention.items():
        if vid_id in data["videos"]:
            data["videos"][vid_id].update(r)

    queue_idx = _queue_index()
    data["template_stats"], data["proc_stats"] = _aggregate(videos, stats, queue_idx)

    _save(data)
    logger.info("Analytics refreshed: %d videos, %d with retention data", len(videos), len(retention))
    return data


# ─── Weekly insights via Ollama ──────────────────────────────────────────────

def weekly_insights() -> str:
    """
    Find top video of last 7 days, compare to channel avg, generate AI recommendations.
    Returns formatted HTML string.
    """
    data    = _load()
    vids    = data.get("videos", {})
    queue_idx = _queue_index()

    shorts  = {k: v for k, v in vids.items() if v.get("is_short")}
    if not shorts:
        return ""

    # Channel averages
    avg_views = round(sum(v.get("views", 0) for v in shorts.values()) / len(shorts))
    pct_vals  = [v["avg_view_pct"] for v in shorts.values() if v.get("avg_view_pct")]
    avg_pct   = round(sum(pct_vals) / len(pct_vals), 1) if pct_vals else 0

    # Top video of last 7 days (by views)
    week_ago = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).isoformat()
    recent   = {k: v for k, v in shorts.items() if v.get("published_at", "") >= week_ago}
    if not recent:
        return ""

    top_id  = max(recent, key=lambda k: recent[k].get("views", 0))
    top     = recent[top_id]
    q_item  = queue_idx.get(top_id, {})

    top_views   = top.get("views", 0)
    top_pct     = top.get("avg_view_pct", 0)
    top_likes   = top.get("likes", 0)
    top_comments = top.get("comments", 0)
    top_title   = top.get("title", top_id)
    top_template = q_item.get("template", "неизвестен")
    top_proc    = q_item.get("procedure", "неизвестна")
    top_url     = f"https://youtube.com/shorts/{top_id}"

    # Context for Ollama
    week_count  = len(recent)
    week_views  = sum(v.get("views", 0) for v in recent.values())
    week_avg    = round(week_views / week_count) if week_count else 0

    # Engagement-метрики: считаем для топ-5 видео недели.
    # - CTA-reach: % дошедших до последних 5 сек (QR на TG).
    # - Swipe-survival: % переживших первые 3 сек (защита KICKER от swipe-away).
    # - Like-rate: likes/views. Главный быстрый позитивный сигнал для shorts-алгоритма;
    #   норма 1-3%, у нас в проде болтается ~0.1% — отсюда виралы не повторяются.
    channel_id = data.get("channel", {}).get("id", "")
    top5_recent = sorted(recent.items(), key=lambda x: x[1].get("views", 0), reverse=True)[:5]
    cta_top5, swipe_top5, like_rates = [], [], []
    top_cta = top_swipe = None
    for vid_id, v in top5_recent:
        views_v = v.get("views", 0)
        likes_v = v.get("likes", 0)
        if views_v >= 50:
            like_rates.append(likes_v / views_v * 100)
        if not channel_id:
            continue
        r_cta = _get_cta_retention(channel_id, vid_id, days=14)
        if r_cta is not None:
            cta_top5.append(r_cta)
            if vid_id == top_id:
                top_cta = r_cta
        r_swipe = _get_swipe_survival(channel_id, vid_id, days=14)
        if r_swipe is not None:
            swipe_top5.append(r_swipe)
            if vid_id == top_id:
                top_swipe = r_swipe
    cta_avg   = round(sum(cta_top5) / len(cta_top5), 1)       if cta_top5   else None
    swipe_avg = round(sum(swipe_top5) / len(swipe_top5), 1)   if swipe_top5 else None
    like_avg  = round(sum(like_rates) / len(like_rates), 2)   if like_rates else None
    extra_lines = []
    if cta_avg is not None:
        extra_lines.append(f"- CTA-reach top-5: {cta_avg}% (доля доживающих до последних 5 сек, где QR на TG)")
    if swipe_avg is not None:
        extra_lines.append(f"- Swipe-survival top-5: {swipe_avg}% (доля переживших первые 3 сек — KICKER)")
    if like_avg is not None:
        extra_lines.append(f"- Like-rate top-5: {like_avg}% (норма shorts 1-3%, главный быстрый сигнал алгоритма)")
    cta_line = "\n" + "\n".join(extra_lines) if extra_lines else ""

    prompt = f"""Ты аналитик YouTube-канала о косметологии. Дай 3-4 конкретные рекомендации по данным за неделю.

ДАННЫЕ НЕДЕЛИ:
- Опубликовано шортов: {week_count}
- Суммарные просмотры: {week_views}
- Средние просмотры на видео за неделю: {week_avg} (канальный avg: {avg_views}){cta_line}

ЛУЧШЕЕ ВИДЕО НЕДЕЛИ:
- Название: «{top_title}»
- Просмотры: {top_views} (в {round(top_views/avg_views, 1) if avg_views else '?'}x выше среднего)
- Retention (AVD%): {top_pct}% (канальный avg: {avg_pct}%){f' / CTA-reach: {top_cta}%' if top_cta is not None else ''}
- Лайки: {top_likes}, комментарии: {top_comments}
- Шаблон: {top_template}
- Процедура: {top_proc}
- URL: {top_url}

ЗАДАЧА:
1. Одним предложением — почему это видео сработало лучше других
2. 3 конкретные рекомендации для следующей недели (что снимать, какой формат, какие процедуры)
3. Одно предупреждение — что НЕ делать

Отвечай по-русски, коротко и конкретно. Без воды. Формат: обычный текст, без markdown."""

    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
        }, timeout=OLLAMA_TIMEOUT)
        resp.raise_for_status()
        analysis = resp.json()["message"]["content"].strip()
    except Exception as e:
        logger.warning("Ollama weekly_insights failed: %s", e)
        analysis = "Анализ недоступен (Ollama не ответил)."

    lines = [
        f"🔥 <b>Топ недели:</b> <a href='{top_url}'>{top_title[:60]}</a>",
        f"👁 {top_views:,} просмотров",
    ]
    if top_pct:
        vs = f" (канал avg: {avg_pct}%)" if avg_pct else ""
        lines.append(f"⏱ Retention: {top_pct}%{vs}")
    if top_swipe is not None:
        lines.append(f"👀 Swipe-survival (3s): {top_swipe}%")
    if top_cta is not None:
        lines.append(f"🎯 CTA-reach: {top_cta}% (доходит до QR на TG)")
    top_views_safe = top.get("views", 0)
    top_likes_safe = top.get("likes", 0)
    if top_views_safe >= 50:
        lr = round(top_likes_safe / top_views_safe * 100, 2)
        marker = " ⚠️" if lr < 0.5 else (" ✅" if lr >= 1 else "")
        lines.append(f"👍 Like-rate: {lr}% (норма 1-3%){marker}")
    if cta_avg is not None and len(cta_top5) > 1:
        lines.append(f"🎯 CTA-reach top-5 avg: {cta_avg}%")
    if like_avg is not None and len(like_rates) > 1:
        lines.append(f"👍 Like-rate top-5 avg: {like_avg}%")
    lines += [
        f"🎬 Шаблон: <code>{top_template}</code> / процедура: <code>{top_proc}</code>",
        "",
        f"🤖 <b>AI-анализ:</b>\n{analysis}",
    ]
    return "\n".join(lines)


# ─── Formatting for bot ──────────────────────────────────────────────────────

def format_stats() -> str:
    data = _load()
    if not data.get("updated_at"):
        return "Аналитика ещё не собиралась. Используй /stats refresh"

    ch   = data.get("channel", {})
    vids = data.get("videos", {})
    tmpl = data.get("template_stats", {})
    proc = data.get("proc_stats", {})

    shorts      = {k: v for k, v in vids.items() if v.get("is_short")}
    total_views = sum(v.get("views", 0) for v in shorts.values())
    avg_views   = round(total_views / len(shorts)) if shorts else 0
    pct_vals    = [v["avg_view_pct"] for v in shorts.values() if v.get("avg_view_pct")]
    avg_pct     = round(sum(pct_vals) / len(pct_vals), 1) if pct_vals else None

    updated = data["updated_at"][:10]
    lines   = [
        f"📊 <b>{ch.get('title', 'Канал')}</b> (обновлено {updated})\n",
        f"Подписчиков: <b>{ch.get('subscribers', 0):,}</b>",
        f"Шортов опубликовано: <b>{len(shorts)}</b>",
        f"Среднее просмотров: <b>{avg_views:,}</b>",
    ]
    if avg_pct is not None:
        lines.append(f"Среднее удержание: <b>{avg_pct}%</b>")
    lines.append("")

    if tmpl:
        # Сортируем по avg_lift (нормализация по процедуре) — это «честный» вклад
        # шаблона, очищенный от того что некоторые процедуры массовее.
        # Если avg_lift нет (старые данные) — фолбэк на avg views.
        sorted_tmpl = sorted(tmpl.items(),
                             key=lambda x: x[1].get("avg_lift", 0) or x[1]["avg"] / 1000,
                             reverse=True)
        lines.append("🎬 <b>Шаблоны</b> (avg lift × процедура → avg views):")
        max_lift = max((s.get("avg_lift", 0) for _, s in sorted_tmpl), default=0)
        for t, s in sorted_tmpl[:6]:
            lift = s.get("avg_lift")
            if lift is not None and max_lift:
                bar = "▓" * min(10, max(1, round(lift / max_lift * 10)))
                lift_str = f"×{lift}"
            else:
                bar = "▓" * 3
                lift_str = "?"
            lines.append(f"  {bar} <code>{t}</code> — {lift_str} / {s['avg']:,} avg ({s['count']} видео)")
        lines.append("")

    if proc:
        sorted_proc = sorted(proc.items(), key=lambda x: x[1]["avg"], reverse=True)
        lines.append("💉 <b>Процедуры</b> (avg просмотров):")
        for p, s in sorted_proc[:6]:
            lines.append(f"  <code>{p}</code> — {s['avg']:,} ({s['count']} видео)")
        lines.append("")

    if shorts:
        top5 = sorted(shorts.items(), key=lambda x: x[1].get("views", 0), reverse=True)[:5]
        lines.append("🔥 <b>Топ-5 шортов:</b>")
        for vid_id, v in top5:
            pct_str = f" · {v['avg_view_pct']}% ret" if v.get("avg_view_pct") else ""
            title   = v.get("title", vid_id)[:45]
            lines.append(f"  {v.get('views', 0):,} 👁{pct_str}  {title}")

    if tmpl and len(tmpl) >= 2:
        best  = max(tmpl.items(), key=lambda x: x[1]["avg"])[0]
        worst = min(tmpl.items(), key=lambda x: x[1]["avg"])[0]
        lines.append(f"\n📌 <b>Рекомендация:</b> больше <code>{best}</code>, меньше <code>{worst}</code>")

    return "\n".join(lines)


def get_template_weights() -> dict[str, float]:
    """Return multipliers for content_plan template selection (best=2.0, worst=0.5)."""
    data = _load()
    tmpl = data.get("template_stats", {})
    if not tmpl:
        return {}
    avgs  = {k: v["avg"] for k, v in tmpl.items()}
    max_v = max(avgs.values())
    min_v = min(avgs.values())
    span  = max_v - min_v or 1
    return {k: round(0.5 + 1.5 * (v - min_v) / span, 2) for k, v in avgs.items()}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    data = refresh()
    print(format_stats())
    print("\n" + "─" * 40)
    print(weekly_insights())
