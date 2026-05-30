"""
Competitor & multilingual YouTube Shorts research.
Searches top-performing shorts → downloads audio → transcribes → analyzes structure.

Run standalone:  python3 yt_research.py
Bot:             import yt_research; yt_research.run_research()
"""
import json
import logging
import re
import os
import datetime
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

BASE_DIR       = Path(__file__).parent
RESEARCH_FILE  = BASE_DIR / "research_cache.json"
AUDIO_DIR      = BASE_DIR / "research_audio"
AUDIO_DIR.mkdir(exist_ok=True)

from config import OLLAMA_URL, OLLAMA_MODEL as MODEL, OLLAMA_TIMEOUT, atomic_write_json

# ─── Search keywords ─────────────────────────────────────────────────────────
# Группы по языку: найдём топ-шорты в каждой языковой нише

SEARCH_QUERIES = {
    "ru": [
        "уход после лазерной шлифовки",
        "уход после биоревитализации",
        "уход после химического пилинга",
        "уход после RF лифтинга",
        "постпроцедурный уход кожа",
    ],
    "en": [
        "skincare after laser treatment shorts",
        "post procedure skincare routine shorts",
        "chemical peel aftercare shorts",
        "microneedling aftercare tips shorts",
        "skincare mistakes after facial shorts",
    ],
    "ko": [
        "레이저 후 피부 관리 shorts",
        "시술 후 스킨케어 shorts",
    ],
    "fr": [
        "soin après laser shorts",
        "routine après peeling shorts",
    ],
}

# Сколько топ-видео анализировать по каждому запросу
TOP_N        = 5
# Сколько видео скачивать и транскрибировать (дорогая операция)
TRANSCRIBE_N = 2


# ─── YouTube API ─────────────────────────────────────────────────────────────

def _yt():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    token_file = BASE_DIR / "yt_token.json"
    scopes = ["https://www.googleapis.com/auth/youtube"]
    creds = Credentials.from_authorized_user_file(str(token_file), scopes)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _search_shorts(yt, query: str, max_results: int = TOP_N) -> list:
    """Search YouTube for Shorts matching query. Returns list of video dicts."""
    resp = yt.search().list(
        part="snippet",
        q=query + " #shorts",
        type="video",
        videoDuration="short",      # < 4 min filter (API can't filter ≤60s directly)
        order="viewCount",
        maxResults=max_results * 2, # fetch extra to filter by actual duration
        relevanceLanguage=None,     # don't restrict — we want multilingual
    ).execute()

    video_ids = [item["id"]["videoId"] for item in resp.get("items", [])]
    if not video_ids:
        return []

    # Fetch stats + duration to filter true Shorts (≤ 90s)
    stats_resp = yt.videos().list(
        part="statistics,contentDetails,snippet",
        id=",".join(video_ids)
    ).execute()

    results = []
    for item in stats_resp.get("items", []):
        dur = item["contentDetails"]["duration"]
        if not _is_short(dur):
            continue
        s = item["statistics"]
        results.append({
            "id":           item["id"],
            "title":        item["snippet"]["title"],
            "channel":      item["snippet"]["channelTitle"],
            "language":     item["snippet"].get("defaultAudioLanguage", "?"),
            "published_at": item["snippet"]["publishedAt"],
            "views":        int(s.get("viewCount", 0)),
            "likes":        int(s.get("likeCount", 0)),
            "comments":     int(s.get("commentCount", 0)),
            "duration":     dur,
            "url":          f"https://youtube.com/shorts/{item['id']}",
        })
        if len(results) >= max_results:
            break

    return sorted(results, key=lambda x: x["views"], reverse=True)


def _is_short(iso_duration: str) -> bool:
    m = re.match(r"PT(?:(\d+)M)?(?:(\d+)S)?", iso_duration)
    if not m:
        return False
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0) <= 90


# ─── Download + Transcribe ───────────────────────────────────────────────────

def _download_audio(video_id: str) -> Path | None:
    """Download audio track of a YouTube video. Returns path to .mp3."""
    out = AUDIO_DIR / f"{video_id}.mp3"
    if out.exists():
        return out
    try:
        import yt_dlp
        opts = {
            "format":            "bestaudio/best",
            "outtmpl":           str(AUDIO_DIR / f"{video_id}.%(ext)s"),
            "postprocessors":    [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
            "quiet":             True,
            "no_warnings":       True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([f"https://youtube.com/shorts/{video_id}"])
        return out if out.exists() else None
    except Exception as e:
        logger.warning("Download failed %s: %s", video_id, e)
        return None


def _transcribe(audio_path: Path) -> str:
    """Transcribe audio using Whisper (multilingual, auto-detects language)."""
    import whisper
    model = whisper.load_model("small")  # ~460MB, good quality multilingual
    result = model.transcribe(str(audio_path), task="translate")  # translate → English
    return result["text"].strip()


# ─── Ollama analysis ─────────────────────────────────────────────────────────

STRUCTURE_PROMPT = """You are analyzing viral YouTube Shorts about post-procedure skincare.
The video has {views} views. Here is its transcript (translated to English):

\"\"\"{transcript}\"\"\"

Analyze and respond ONLY with valid JSON (no extra text):
{{
  "hook_type": "one of: fear/pain_point | curiosity_gap | direct_address | shocking_fact | promise",
  "hook_text": "the actual hook sentence from the transcript",
  "structure": ["block1 description", "block2 description", "block3 description"],
  "cta_style": "question | subscribe | follow | buy | comment",
  "cta_text": "the actual CTA from the transcript",
  "why_it_works": "1-2 sentences on why this structure gets views",
  "reusable_formula": "a generic formula that can be applied to other procedures"
}}"""


def _analyze_structure(transcript: str, views: int) -> dict:
    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": STRUCTURE_PROMPT.format(
                transcript=transcript[:1500], views=views
            )}],
            "stream": False,
            "keep_alive": "24h",
            "think": False,
            "format": "json",
            "options": {"temperature": 0.3, "top_p": 0.9},
        },
        timeout=OLLAMA_TIMEOUT,
    )
    resp.raise_for_status()
    raw   = resp.json()["message"]["content"]
    match = re.search(r'\{[\s\S]+\}', raw)
    return json.loads(match.group()) if match else {"raw": raw}


TITLE_PATTERNS_PROMPT = """You are analyzing titles of top YouTube Shorts about skincare after cosmetic procedures.

Top performing titles (sorted by views):
{titles_list}

Identify and respond ONLY with valid JSON:
{{
  "top_patterns": [
    "pattern description with example",
    "pattern description with example",
    "pattern description with example"
  ],
  "best_hooks": ["hook phrase 1", "hook phrase 2", "hook phrase 3"],
  "avoid": ["pattern to avoid 1", "pattern to avoid 2"],
  "insight": "key insight about what makes these titles work"
}}"""


def _analyze_titles(videos: list, lang: str) -> dict:
    titles = "\n".join(
        f"{i+1}. [{v['views']:,} views] {v['title']}"
        for i, v in enumerate(videos[:10])
    )
    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": TITLE_PATTERNS_PROMPT.format(titles_list=titles)}],
            "stream":    False,
            "keep_alive": "24h",
            "think":     False,
            "format":    "json",
            "options":   {"temperature": 0.3},
        },
        timeout=OLLAMA_TIMEOUT,
    )
    resp.raise_for_status()
    raw   = resp.json()["message"]["content"]
    match = re.search(r'\{[\s\S]+\}', raw)
    return json.loads(match.group()) if match else {"raw": raw}


# ─── Main research loop ──────────────────────────────────────────────────────

def run_research(languages: list[str] | None = None, transcribe: bool = True) -> dict:
    """
    Run full research: search → collect stats → analyze titles → transcribe+analyze top videos.
    Saves results to research_cache.json.
    """
    yt   = _yt()
    data = _load_research()
    langs = languages or list(SEARCH_QUERIES.keys())

    for lang in langs:
        queries = SEARCH_QUERIES.get(lang, [])
        for query in queries:
            logger.info("Searching [%s]: %s", lang, query)
            try:
                videos = _search_shorts(yt, query)
            except Exception as e:
                logger.warning("Search failed: %s", e)
                continue

            if not videos:
                continue

            # Store raw results
            key = f"{lang}:{query}"
            data["searches"][key] = {
                "lang":        lang,
                "query":       query,
                "fetched_at":  datetime.datetime.utcnow().isoformat(),
                "videos":      videos,
            }

            # Analyze title patterns via Ollama
            try:
                patterns = _analyze_titles(videos, lang)
                data["searches"][key]["title_patterns"] = patterns
                logger.info("Title patterns analyzed for: %s", query)
            except Exception as e:
                logger.warning("Title analysis failed: %s", e)

            # Transcribe + deep-analyze top N videos
            if transcribe:
                for video in videos[:TRANSCRIBE_N]:
                    vid_id = video["id"]
                    if vid_id in data["transcripts"]:
                        continue  # already done
                    logger.info("Downloading %s (%s views)…", video["title"][:40], video["views"])
                    audio = _download_audio(vid_id)
                    if not audio:
                        continue
                    try:
                        transcript = _transcribe(audio)
                        analysis   = _analyze_structure(transcript, video["views"])
                        data["transcripts"][vid_id] = {
                            "title":      video["title"],
                            "views":      video["views"],
                            "url":        video["url"],
                            "lang":       lang,
                            "transcript": transcript,
                            "analysis":   analysis,
                        }
                        logger.info("Analyzed: %s", vid_id)
                    except Exception as e:
                        logger.warning("Transcribe/analyze failed %s: %s", vid_id, e)

    data["updated_at"] = datetime.datetime.utcnow().isoformat()
    _save_research(data)
    return data


def _load_research() -> dict:
    if RESEARCH_FILE.exists():
        return json.loads(RESEARCH_FILE.read_text(encoding="utf-8"))
    return {"updated_at": None, "searches": {}, "transcripts": {}}


def _save_research(data: dict):
    atomic_write_json(RESEARCH_FILE, data)


# ─── Formatting ──────────────────────────────────────────────────────────────

def format_research_summary() -> str:
    data = _load_research()
    if not data.get("updated_at"):
        return "Исследование ещё не запускалось. Используй /research run"

    searches    = data.get("searches", {})
    transcripts = data.get("transcripts", {})
    updated     = data["updated_at"][:10]

    lines = [f"🔍 <b>Исследование конкурентов</b> (обновлено {updated})\n"]

    # Aggregate top videos across all searches
    all_videos = []
    for s in searches.values():
        all_videos.extend(s.get("videos", []))
    all_videos.sort(key=lambda x: x["views"], reverse=True)
    seen = set()
    top_videos = []
    for v in all_videos:
        if v["id"] not in seen:
            seen.add(v["id"])
            top_videos.append(v)

    lines.append(f"Запросов проанализировано: <b>{len(searches)}</b>")
    lines.append(f"Видео найдено: <b>{len(top_videos)}</b>")
    lines.append(f"Транскрибировано: <b>{len(transcripts)}</b>\n")

    if top_videos:
        lines.append("🔥 <b>Топ-5 чужих видео по просмотрам:</b>")
        for v in top_videos[:5]:
            lang_flag = {"ru": "🇷🇺", "en": "🇬🇧", "ko": "🇰🇷", "fr": "🇫🇷"}.get(v.get("language", ""), "🌐")
            lines.append(f"  {lang_flag} {v['views']:,} 👁  {v['title'][:55]}")
        lines.append("")

    # Collect hook types from transcripts
    hook_types: dict[str, int] = {}
    formulas = []
    for t in transcripts.values():
        analysis = t.get("analysis", {})
        ht = analysis.get("hook_type")
        if ht:
            hook_types[ht] = hook_types.get(ht, 0) + 1
        f = analysis.get("reusable_formula")
        if f:
            formulas.append(f)

    if hook_types:
        sorted_hooks = sorted(hook_types.items(), key=lambda x: x[1], reverse=True)
        lines.append("🎣 <b>Самые частые типы хуков:</b>")
        for ht, cnt in sorted_hooks:
            lines.append(f"  <code>{ht}</code> — {cnt}×")
        lines.append("")

    if formulas:
        lines.append("💡 <b>Готовые формулы из топ-видео:</b>")
        for f in formulas[:3]:
            lines.append(f"  • {f[:120]}")

    return "\n".join(lines)


def get_best_hook_examples() -> str:
    """Return hook examples from top transcripts for injection into SCRIPT_SYSTEM."""
    data = _load_research()
    transcripts = data.get("transcripts", {})
    if not transcripts:
        return ""

    top = sorted(transcripts.values(), key=lambda x: x["views"], reverse=True)[:5]
    examples = []
    for t in top:
        hook = t.get("analysis", {}).get("hook_text", "")
        formula = t.get("analysis", {}).get("reusable_formula", "")
        if hook and formula:
            examples.append(f"Hook ({t['views']:,} views): \"{hook}\"\nFormula: {formula}")

    return "\n\n".join(examples)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    # Quick run: RU only, no transcription (fast)
    import sys
    transcribe = "--transcribe" in sys.argv
    langs      = ["en"] if "--en" in sys.argv else (["ru"] if "--ru" in sys.argv else None)
    data = run_research(languages=langs, transcribe=transcribe)
    print(format_research_summary())
