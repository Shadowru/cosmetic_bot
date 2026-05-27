#!/usr/bin/env python3
"""
Subprocess worker for TikTok upload — runs outside asyncio to avoid
Playwright sync/async conflict when called from the Telegram bot.

Usage: python3 tt_upload_worker.py <video_path> <title> <cookies_file>
"""
import json
import sys

video_path, title, cookies_file = sys.argv[1], sys.argv[2], sys.argv[3]

cookies = json.load(open(cookies_file, encoding="utf-8"))

TAGS = "#косметология #уходзакожей #постпроцедурный #скинкер #шортс"
description = f"{title}\n\n{TAGS}"[:2200]

from tiktok_uploader.upload import upload_video

upload_video(
    filename=video_path,
    description=description,
    cookies_list=cookies,
    browser="chromium",
    headless=True,
)
print("OK")
