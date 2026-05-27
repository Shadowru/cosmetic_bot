#!/usr/bin/env python3
"""
Run ONCE on the server to authorize YouTube access.

Setup:
  1. Go to console.cloud.google.com
  2. Create a project → APIs & Services → Enable "YouTube Data API v3"
  3. Credentials → Create OAuth 2.0 Client ID → Desktop app
  4. Download JSON → save as yt_credentials.json next to this file
  5. Run: python3 youtube_auth.py
  6. Open the printed URL in browser, authorize, paste the code back
"""
from pathlib import Path
from google_auth_oauthlib.flow import Flow

BASE_DIR         = Path(__file__).parent
CREDENTIALS_FILE = BASE_DIR / "yt_credentials.json"
TOKEN_FILE       = BASE_DIR / "yt_token.json"
SCOPES           = [
    "https://www.googleapis.com/auth/youtube.force-ssl",   # full incl. commentThreads.insert
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

if not CREDENTIALS_FILE.exists():
    print(f"Файл не найден: {CREDENTIALS_FILE}")
    print()
    print("Инструкция:")
    print("  1. console.cloud.google.com → новый проект")
    print("  2. APIs & Services → Library → включи YouTube Data API v3")
    print("  3. APIs & Services → Credentials → Create Credentials → OAuth client ID")
    print("  4. Application type: Desktop app → Create")
    print(f"  5. Скачай JSON → переименуй в yt_credentials.json → положи в {BASE_DIR}")
    print("  6. Запусти снова")
    raise SystemExit(1)

flow = Flow.from_client_secrets_file(
    str(CREDENTIALS_FILE),
    scopes=SCOPES,
    redirect_uri="urn:ietf:wg:oauth:2.0:oob",
)
auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

print("\nОткрой эту ссылку в браузере:")
print(f"\n  {auth_url}\n")
print("Разреши доступ → скопируй код со страницы → вставь сюда:")
code = input("Код: ").strip()

flow.fetch_token(code=code)
TOKEN_FILE.write_text(flow.credentials.to_json())
print(f"\nГотово. Токен сохранён: {TOKEN_FILE}")
