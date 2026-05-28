"""_preprocess_tts должен заменить числа и латинские аббревиатуры на произносимое."""
from shorts_generator import _preprocess_tts


def test_numbers_to_russian_words():
    out = _preprocess_tts("3 дня")
    assert "три" in out
    assert "3" not in out


def test_spf50_specific_mapping():
    # «SPF 50» → «эс-пэ-эф пятьдесят» (более конкретное правило идёт раньше «SPF»)
    out = _preprocess_tts("крем SPF 50 утром")
    assert "эс-пэ-эф пятьдесят" in out
    assert "SPF" not in out


def test_rf_and_ph():
    out = _preprocess_tts("после RF делать пилинг с pH 4")
    assert "эр-эф" in out
    assert "пэ аш" in out
    assert "четыре" in out


def test_no_digits_no_abbrev_passthrough():
    text = "нормальная реакция кожи без отёка"
    assert _preprocess_tts(text) == text


def test_case_insensitive_abbrev():
    # IPL внутри слова не должен ломаться, но как отдельное слово — заменяется
    out = _preprocess_tts("после IPL процедуры")
    assert "и-пэ-эль" in out
