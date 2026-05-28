"""should_skip_slot — два сигнала: <5 свежих комбо или AVD<22%."""
import json
from unittest.mock import patch

import shorts_generator
from shorts_generator import should_skip_slot, PROCEDURES, TEMPLATES, GENERAL_TEMPLATES, TEMPLATE_BLACKLIST


def _all_combos():
    combos = []
    for p in PROCEDURES:
        if p == "general":
            continue
        for t in TEMPLATES:
            if t in TEMPLATE_BLACKLIST:
                continue
            combos.append({"procedure": p, "template": t, "type": "short", "status": "published"})
    for t in GENERAL_TEMPLATES:
        if t in TEMPLATE_BLACKLIST:
            continue
        combos.append({"procedure": "general", "template": t, "type": "short", "status": "published"})
    return combos


def test_no_used_combos_does_not_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(shorts_generator, "load_queue", lambda: [])
    monkeypatch.setattr(shorts_generator, "BASE_DIR", tmp_path)  # no analytics.json
    skip, reason = should_skip_slot()
    assert skip is False


def test_skip_when_fewer_than_min_fresh_combos(tmp_path, monkeypatch):
    all_combos = _all_combos()
    # Оставляем только 4 свежих — это <MIN_FRESH_COMBOS (5).
    used = all_combos[:-4]
    monkeypatch.setattr(shorts_generator, "load_queue", lambda: used)
    monkeypatch.setattr(shorts_generator, "BASE_DIR", tmp_path)
    skip, reason = should_skip_slot()
    assert skip is True
    assert "свежих" in reason


def test_skip_when_avd_below_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(shorts_generator, "load_queue", lambda: [])
    analytics = {
        "videos": {
            f"vid{i}": {
                "is_short": True,
                "avg_view_pct": 15.0,  # ниже 22%
                "published_at": f"2026-05-{i:02d}T12:00:00",
            }
            for i in range(1, 11)
        }
    }
    (tmp_path / "analytics.json").write_text(json.dumps(analytics))
    monkeypatch.setattr(shorts_generator, "BASE_DIR", tmp_path)
    skip, reason = should_skip_slot()
    assert skip is True
    assert "AVD" in reason


def test_no_skip_when_avd_healthy(tmp_path, monkeypatch):
    monkeypatch.setattr(shorts_generator, "load_queue", lambda: [])
    analytics = {
        "videos": {
            f"vid{i}": {
                "is_short": True,
                "avg_view_pct": 35.0,  # выше 22%
                "published_at": f"2026-05-{i:02d}T12:00:00",
            }
            for i in range(1, 11)
        }
    }
    (tmp_path / "analytics.json").write_text(json.dumps(analytics))
    monkeypatch.setattr(shorts_generator, "BASE_DIR", tmp_path)
    skip, reason = should_skip_slot()
    assert skip is False


def test_corrupt_analytics_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(shorts_generator, "load_queue", lambda: [])
    (tmp_path / "analytics.json").write_text("{not valid json")
    monkeypatch.setattr(shorts_generator, "BASE_DIR", tmp_path)
    skip, reason = should_skip_slot()
    # Сломанная аналитика — продолжаем (не агрессивный skip)
    assert skip is False
