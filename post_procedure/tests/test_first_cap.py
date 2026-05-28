"""_first_cap не должен ломать аббревиатуры (баг str.capitalize: «SMAS» → «Smas»)."""
from bot import _first_cap


def test_smas_stays_uppercase():
    assert _first_cap("SMAS-лифтинга") == "SMAS-лифтинга"


def test_rf_stays_uppercase():
    assert _first_cap("RF-лифтинга") == "RF-лифтинга"


def test_lowercases_first_only_when_already_lowercase():
    assert _first_cap("ошибки после биоревитализации") == "Ошибки после биоревитализации"


def test_empty_string_passthrough():
    assert _first_cap("") == ""


def test_already_capitalized_unchanged():
    assert _first_cap("Норма или тревога") == "Норма или тревога"


def test_single_char():
    assert _first_cap("a") == "A"
