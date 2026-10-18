"""Расчёт Матрицы Судьбы (методика Н. Ладини). Чистые функции, без aiogram.

Все формулы собраны здесь: при расхождении с эталонным калькулятором правка — в одном месте.
"""
from dataclasses import dataclass
from datetime import date


def digit_sum(n: int) -> int:
    return sum(int(ch) for ch in str(n))


def reduce(n: int) -> int:
    """Приведение к аркану: пока n > 22 — заменять на сумму цифр. Результат 1..22."""
    if n < 1:
        raise ValueError(f"Нельзя привести к аркану: {n}")
    while n > 22:
        n = digit_sum(n)
    return n


@dataclass(frozen=True)
class Matrix:
    a: int  # личность (день)
    b: int  # таланты (месяц)
    c: int  # материальная сфера (год)
    d: int  # нижняя точка, основа кармического хвоста
    e: int  # центр, зона комфорта
    sky: int  # Небо = B + D
    earth: int  # Земля = A + C
    purpose: int  # личное предназначение = Небо + Земля
    money_channel: int  # C + E
    h: int  # отношения: C + D
    relationships_channel: int  # H + E
    d1: int  # D + E
    d2: int  # D + d1

    @property
    def karmic_tail(self) -> str:
        return f"{self.d2}-{self.d1}-{self.d}"


@dataclass(frozen=True)
class CompatMatrix:
    a: int
    b: int
    c: int
    d: int
    e: int
    main: int  # главное число пары = E1 + E2


def calculate(birth_date: date) -> Matrix:
    a = reduce(birth_date.day)
    b = reduce(birth_date.month)
    c = reduce(digit_sum(birth_date.year))
    d = reduce(a + b + c)
    e = reduce(a + b + c + d)

    sky = reduce(b + d)
    earth = reduce(a + c)
    purpose = reduce(sky + earth)

    h = reduce(c + d)
    d1 = reduce(d + e)
    d2 = reduce(d + d1)

    return Matrix(
        a=a, b=b, c=c, d=d, e=e,
        sky=sky, earth=earth, purpose=purpose,
        money_channel=reduce(c + e),
        h=h, relationships_channel=reduce(h + e),
        d1=d1, d2=d2,
    )


def year_forecast(m: Matrix, year: int) -> int:
    """Аркан года: A + B + (сумма цифр года)."""
    return reduce(m.a + m.b + reduce(digit_sum(year)))


def personal_day(m: Matrix, day: date) -> int:
    """Число дня: аркан личности + приведённые день и месяц даты."""
    return reduce(m.a + reduce(day.day) + reduce(day.month))


def compatibility(m1: Matrix, m2: Matrix) -> CompatMatrix:
    return CompatMatrix(
        a=reduce(m1.a + m2.a),
        b=reduce(m1.b + m2.b),
        c=reduce(m1.c + m2.c),
        d=reduce(m1.d + m2.d),
        e=reduce(m1.e + m2.e),
        main=reduce(m1.e + m2.e),
    )
