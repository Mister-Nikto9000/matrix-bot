"""Ожидаемые значения посчитаны вручную по формулам из MVP_SPEC.md, раздел 4."""
from datetime import date

import pytest

from bot.services.matrix_calc import calculate, compatibility, reduce, year_forecast

# дата: (A, B, C, D, E, Небо, Земля, Предназначение, ден.канал, H, канал отношений, d1, d2, хвост)
CASES = {
    date(1994, 3, 7): (7, 3, 5, 15, 3, 18, 12, 3, 8, 20, 5, 18, 6, "6-18-15"),
    date(2000, 2, 29): (11, 2, 2, 15, 3, 17, 13, 3, 5, 17, 20, 18, 6, "6-18-15"),
    date(1999, 12, 31): (4, 12, 10, 8, 7, 20, 14, 7, 17, 18, 7, 15, 5, "5-15-8"),
    date(1985, 11, 23): (5, 11, 5, 21, 6, 5, 10, 15, 11, 8, 14, 9, 3, "3-9-21"),
    date(2001, 1, 1): (1, 1, 3, 5, 10, 6, 4, 10, 13, 8, 18, 15, 20, "20-15-5"),
    date(1978, 6, 22): (22, 6, 7, 8, 7, 14, 11, 7, 14, 15, 22, 15, 5, "5-15-8"),
    date(2010, 8, 15): (15, 8, 3, 8, 7, 16, 18, 7, 10, 11, 18, 15, 5, "5-15-8"),
    date(1920, 9, 30): (3, 9, 12, 6, 3, 15, 15, 3, 15, 18, 21, 9, 15, "15-9-6"),
    date(1988, 2, 29): (11, 2, 8, 21, 6, 5, 19, 6, 14, 11, 17, 9, 3, "3-9-21"),
    date(2024, 1, 31): (4, 1, 8, 13, 8, 14, 12, 8, 16, 21, 11, 21, 7, "7-21-13"),
    date(1969, 7, 19): (19, 7, 7, 6, 12, 13, 8, 21, 19, 13, 7, 18, 6, "6-18-6"),
    date(2005, 10, 28): (10, 10, 7, 9, 9, 19, 17, 9, 16, 16, 7, 18, 9, "9-18-9"),
}


@pytest.mark.parametrize("n, expected", [(1, 1), (22, 22), (23, 5), (31, 4), (99, 18), (199, 19), (999, 9)])
def test_reduce(n, expected):
    assert reduce(n) == expected


def test_reduce_rejects_zero():
    with pytest.raises(ValueError):
        reduce(0)


@pytest.mark.parametrize("birth_date", list(CASES))
def test_calculate(birth_date):
    m = calculate(birth_date)
    got = (m.a, m.b, m.c, m.d, m.e, m.sky, m.earth, m.purpose, m.money_channel,
           m.h, m.relationships_channel, m.d1, m.d2, m.karmic_tail)
    assert got == CASES[birth_date]


@pytest.mark.parametrize("birth_date", list(CASES))
def test_all_points_are_arcana(birth_date):
    m = calculate(birth_date)
    for name in ("a", "b", "c", "d", "e", "sky", "earth", "purpose", "money_channel",
                 "h", "relationships_channel", "d1", "d2"):
        assert 1 <= getattr(m, name) <= 22, name


def test_year_forecast():
    # 07.03.1994 в 2026: 7 + 3 + reduce(2+0+2+6=10) = 20
    assert year_forecast(calculate(date(1994, 3, 7)), 2026) == 20


def test_compatibility():
    c = compatibility(calculate(date(1994, 3, 7)), calculate(date(1999, 12, 31)))
    assert (c.a, c.b, c.c, c.d, c.e, c.main) == (11, 15, 15, 5, 10, 10)


def test_compatibility_is_symmetric():
    m1, m2 = calculate(date(1985, 11, 23)), calculate(date(2000, 2, 29))
    assert compatibility(m1, m2) == compatibility(m2, m1)
