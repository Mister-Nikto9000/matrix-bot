from datetime import date

import pytest

from bot.services.dates import format_date, parse_birth_date

TODAY = date(2026, 10, 15)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("07.03.1994", date(1994, 3, 7)),
        ("7.3.1994", date(1994, 3, 7)),
        (" 29.02.2000 ", date(2000, 2, 29)),
        ("31-12-1999", date(1999, 12, 31)),
        ("01/01/1920", date(1920, 1, 1)),
        ("15.10.2026", date(2026, 10, 15)),
    ],
)
def test_valid(text, expected):
    assert parse_birth_date(text, TODAY) == expected


@pytest.mark.parametrize(
    "text",
    ["", "привет", "1994-03-07", "29.02.2001", "31.04.1990", "00.01.1990", "12.13.1990",
     "31.12.1919", "16.10.2026", "01.01.2027", "07.03.94"],
)
def test_invalid(text):
    assert parse_birth_date(text, TODAY) is None


def test_format():
    assert format_date(date(1994, 3, 7)) == "07.03.1994"
