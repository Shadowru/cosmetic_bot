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
