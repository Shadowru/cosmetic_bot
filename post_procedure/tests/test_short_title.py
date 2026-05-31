"""_short_title должен подставить родительный падеж процедуры и сохранить аббревиатуру."""
from bot import _short_title


def test_smas_lifting_title():
    title = _short_title({"procedure": "smas", "template": "mistakes"})
    assert title == "Ошибки которые делают все после SMAS-лифтинга"


def test_rf_lifting_title():
    title = _short_title({"procedure": "rf", "template": "forbidden"})
    assert title == "Что нельзя делать после RF-лифтинга"


def test_botox_genitive():
    title = _short_title({"procedure": "botox", "template": "days"})
    # PROC_NAMES["botox"] = "ботокса" — родительный, не «после ботокс»
    assert "ботокса" in title
    assert "после ботокс " not in title


def test_general_template():
    title = _short_title({"procedure": "general", "template": "money"})
    assert title.startswith("Деньги")


# --- first_time: критический шаблон где раньше «делаешь {proc} впервые»
# падал в родительный из PROC_NAMES («делаешь лазерной шлифовки впервые»).
# Защита от регрессии: проверяем процедуры разного рода и аббревиатур.

def test_first_time_laser_no_genitive_collision():
    title = _short_title({"procedure": "laser", "template": "first_time"})
    # Сейчас правильно: «...впервые после лазерной шлифовки...»
    # Регрессия (старая форма): «делаешь лазерной шлифовки впервые»
    assert "делаешь лазерной" not in title.lower()
    assert "после лазерной шлифовки" in title.lower()


def test_first_time_smas_keeps_uppercase():
    title = _short_title({"procedure": "smas", "template": "first_time"})
    assert "SMAS-лифтинга" in title  # аббревиатура не должна сломаться
    assert "делаешь smas" not in title.lower()


def test_first_time_botox_masculine():
    title = _short_title({"procedure": "botox", "template": "first_time"})
    assert "после ботокса" in title.lower()
    assert "делаешь ботокса" not in title.lower()
