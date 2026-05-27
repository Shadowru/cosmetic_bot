import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR     = Path(__file__).parent
COOKIES_FILE = BASE_DIR / "tiktok_cookies.json"

DESCRIPTION_TAGS = "#косметология #уходзакожей #постпроцедурный #скинкер #шортс"


def upload_short(video_path: str, title: str) -> str:
    """Upload video to TikTok via subprocess worker (avoids Playwright sync/async conflict)."""
    import subprocess
    import sys

    if not COOKIES_FILE.exists():
        raise RuntimeError(f"TikTok не настроен: файл {COOKIES_FILE} не найден.")

    worker = BASE_DIR / "tt_upload_worker.py"
    result = subprocess.run(
        [sys.executable, str(worker), video_path, title, str(COOKIES_FILE)],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "unknown error").strip()
        raise RuntimeError(err)
    logger.info("TikTok uploaded: %s", result.stdout.strip())
    return "https://www.tiktok.com/@posleprocedur"


def is_authorized() -> bool:
    return COOKIES_FILE.exists()
