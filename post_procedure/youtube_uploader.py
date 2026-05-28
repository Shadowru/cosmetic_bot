import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR         = Path(__file__).parent
CREDENTIALS_FILE = BASE_DIR / "yt_credentials.json"
TOKEN_FILE       = BASE_DIR / "yt_token.json"
SCOPES           = [
    "https://www.googleapis.com/auth/youtube.force-ssl",  # commentThreads.insert требует force-ssl, не просто youtube
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def _get_client():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
        else:
            raise RuntimeError(
                "YouTube не авторизован. Запусти: python3 youtube_auth.py"
            )

    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload_short(video_path: str, title: str, procedure: str = "", publish_at=None) -> str:
    """Upload video as YouTube Short. Returns URL like https://youtube.com/shorts/{id}.

    publish_at: datetime (UTC) — if set, video is scheduled (private until that time).
    """
    from googleapiclient.http import MediaFileUpload

    youtube = _get_client()

    tags = ["уход за кожей", "косметология", "постпроцедурный уход", "shorts"]
    description = (
        "Уход после косметологических процедур — схемы по дням, продукты, советы.\n"
        "Список средств по дням: t.me/posleprocedur\n\n"
        "#Shorts #уходзакожей #косметология #постпроцедурный"
    )

    status = {"selfDeclaredMadeForKids": False}
    if publish_at:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    else:
        status["privacyStatus"] = "public"

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": tags,
            "categoryId": "26",        # Howto & Style
            "defaultLanguage": "ru",
        },
        "status": status,
    }

    media = MediaFileUpload(
        video_path, mimetype="video/mp4", resumable=True, chunksize=2 * 1024 * 1024
    )
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("YouTube upload: %d%%", int(status.progress() * 100))

    video_id = response["id"]
    _post_tg_comment(youtube, video_id, procedure)
    url = f"https://youtube.com/shorts/{video_id}"
    logger.info("YouTube Shorts published: %s", url)
    return url


_PROC_GENITIVE = {
    "laser":       "лазерной шлифовки",
    "biorevit":    "биоревитализации",
    "piling":      "пилинга",
    "rf":          "RF-лифтинга",
    "dermaroller": "дермароллера",
    "chistka":     "чистки лица",
    "meso":        "мезотерапии",
    "botox":       "ботокса",
    "fillers":     "филлеров",
    "plazma":      "плазмолифтинга",
    "photo":       "фотоомоложения",
    "smas":        "SMAS-лифтинга",
    "general":     "процедуры",
}


import random as _random


_COMMENT_QUESTIONS = {
    "laser":       "А ты как ухаживала после лазера? Сколько дней не выходила из дома?",
    "biorevit":    "У тебя были папулы после биоревитализации? Как долго проходили?",
    "piling":      "Какой пилинг переносила тяжелее всего — поверхностный или срединный?",
    "rf":          "Заметила результат после первой RF, или нужно было больше процедур?",
    "dermaroller": "На какой день после дермароллера кожа выглядела хуже всего?",
    "chistka":     "После чистки тоже бывает «эффект хуже стало»? Сколько дней проходит?",
    "meso":        "Какой состав мезококтейля брала и стоило ли оно того?",
    "botox":       "У тебя был эффект «маски» после ботокса или всё естественно?",
    "fillers":     "Какие филлеры держатся дольше — на гиалуронке или коллагене?",
    "plazma":      "Делала плазмолифтинг — сколько дней синяки сходили? Стоит того?",
    "photo":       "После фотоомоложения у тебя были корочки или просто краснота? На сколько дней?",
    "smas":        "У тебя после SMAS отёки держались сколько дней? Когда увидела результат?",
    "general":     "Какая процедура дала тебе самый заметный результат — и какая разочаровала?",
}


def _post_tg_comment(youtube, video_id: str, procedure: str) -> None:
    """Post engagement-driving question as first comment.
    Goal: trigger replies (algorithm signal), not drive away to Telegram.
    """
    question = _COMMENT_QUESTIONS.get(procedure, _COMMENT_QUESTIONS["general"])
    text = f"{question} 👇"
    try:
        youtube.commentThreads().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {"snippet": {"textOriginal": text}},
                }
            },
        ).execute()
        logger.info("Comment posted: %s", video_id)
    except Exception as e:
        logger.error("Comment post FAILED for %s: %s — check OAuth scope (need youtube.force-ssl)", video_id, e)


def is_authorized() -> bool:
    """Check if token.json exists and is still valid (or refreshable)."""
    if not TOKEN_FILE.exists():
        return False
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        if creds.valid:
            return True
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
            return True
    except Exception as e:
        logger.warning("YouTube auth check failed (%s): %s", type(e).__name__, e)
    return False
