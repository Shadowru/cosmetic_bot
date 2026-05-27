"""
Fact-check для высокорисковых шаблонов (сейчас только `norm_alarm`).

Использует OpenAI-совместимый прокси `api.proxyapi.ru` для доступа к Gemini
из РФ-региона (нативный Google AI Studio API заблокирован FAILED_PRECONDITION).
Модель — `google/gemini-3.5-flash`.

Graceful degradation: если `PROXYAPI_KEY` не установлен или API падает,
валидация молча пропускается и контент проходит как обычно.
"""
import json
import logging
import os

import requests

logger = logging.getLogger(__name__)

PROXYAPI_KEY   = os.environ.get("PROXYAPI_KEY", "")
# gemini-3.5-flash > gemini-2.5-flash: меньше false-positive WARN на корректном контенте,
# точнее объяснения. Override через PROXYAPI_MODEL в .env при необходимости.
PROXYAPI_MODEL = os.environ.get("PROXYAPI_MODEL", "gemini-3.5-flash")
# proxyapi.ru проксирует native Google AI endpoint (а не OpenAI-совместимый).
# OpenAI-путь /openai/v1 у них поддерживает только OpenAI-модели. Gemini — здесь:
PROXYAPI_URL   = f"https://api.proxyapi.ru/google/v1beta/models/{PROXYAPI_MODEL}:generateContent"


_NORM_ALARM_PROMPT = """Ты медицинский редактор. Проверь сценарий 30-секундного видео в формате «норма или тревога после {procedure}».

ОЖИДАЕМАЯ СТРУКТУРА:
— block1 и block2: НОРМАЛЬНЫЕ реакции после процедуры (отёк, лёгкая краснота, шелушение, ощущение стянутости и т.п. — то что проходит само в типичные сроки)
— block3: ТРЕВОЖНЫЙ признак, при котором нужно к врачу (высокая температура, гной, обширная аллергическая реакция, сильное кровотечение, резкая боль через дни после процедуры и т.п.)

КРИТИЧЕСКИЕ КРИТЕРИИ (любой провал = "fail"):
1. ОПАСНЫЙ признак назван НОРМОЙ — самое худшее, зритель пропустит реальную проблему.
2. Невозможный / выдуманный для этой процедуры признак.
3. Серьёзно неверные сроки («отёк месяц после ботокса — норма»).
4. Алармизм НОРМАЛЬНОЙ реакции в block1/block2 (паника на пустом месте).

МЕНЕЕ КРИТИЧНОЕ (= "warn"):
— Расплывчатые формулировки, неточности в деталях, не опасные для зрителя.

СЦЕНАРИЙ:
hook: {hook}
block1 (заявлено как норма): {block1}
block2 (заявлено как норма): {block2}
block3 (заявлено как тревога): {block3}

Верни строго JSON без пояснений:
{{
  "severity": "ok" или "warn" или "fail",
  "issues": ["конкретная проблема 1", "конкретная проблема 2"],
  "rationale": "одно предложение почему"
}}"""


def validate_norm_alarm(script: dict, procedure_nom: str) -> tuple[bool, str]:
    """
    Returns (passed, message).
    - passed=True: severity is "ok" or "warn" (warn проходит, но в лог)
    - passed=False: severity is "fail" — нельзя публиковать, нужна регенерация
    Если PROXYAPI_KEY не задан или API упал — passed=True (graceful degradation).
    """
    if not PROXYAPI_KEY:
        logger.info("validate_norm_alarm: skipped (PROXYAPI_KEY not set)")
        return True, "skipped"

    prompt = _NORM_ALARM_PROMPT.format(
        procedure=procedure_nom,
        hook=script.get("hook", ""),
        block1=script.get("block1", ""),
        block2=script.get("block2", ""),
        block3=script.get("block3", ""),
    )
    try:
        resp = requests.post(
            f"{PROXYAPI_URL}?key={PROXYAPI_KEY}",
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "response_mime_type": "application/json",
                },
            },
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
        severity  = result.get("severity", "ok")
        issues    = result.get("issues", [])
        rationale = result.get("rationale", "")
        passed    = severity != "fail"
        issue_str = "; ".join(issues) if issues else "—"
        msg = f"[{severity}] {rationale} (issues: {issue_str})"
        if severity == "fail":
            logger.error("validate_norm_alarm FAILED: %s", msg)
        elif severity == "warn":
            logger.warning("validate_norm_alarm WARN: %s", msg)
        else:
            logger.info("validate_norm_alarm OK: %s", rationale)
        return passed, msg
    except Exception as e:
        body = ""
        if 'resp' in locals():
            try: body = f" body={resp.text[:200]}"
            except: pass
        logger.warning("validate_norm_alarm: API error (%s)%s — accepting by default", e, body)
        return True, f"validator error: {e}"
