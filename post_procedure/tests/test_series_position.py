"""_series_position должен возвращать 1-based index или None для несерийных комбо."""
from shorts_generator import _series_position, SERIES


def test_first_in_series():
    # biorevit: [("biorevit", "days"), ("biorevit", "norm_alarm"), ...]
    assert _series_position("biorevit", "days") == (1, 3)


def test_last_in_series():
    proc, items = next(iter(SERIES.items()))
    p, t = items[-1]
    assert _series_position(p, t) == (len(items), len(items))


def test_not_in_series_returns_none():
    # «mistakes» отсутствует у biorevit в SERIES
    assert _series_position("biorevit", "mistakes") is None


def test_unknown_procedure_returns_none():
    assert _series_position("unknown_proc", "days") is None


def test_norm_alarm_is_always_in_series():
    # По дизайну: каждый norm_alarm — третий элемент серии своей процедуры
    for proc, items in SERIES.items():
        if any(t == "norm_alarm" for _, t in items):
            pos = _series_position(proc, "norm_alarm")
            assert pos is not None
            idx, total = pos
            assert 1 <= idx <= total
