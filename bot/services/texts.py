"""Загрузка и выдача текстов арканов из content/arcana.json.

Любое поле аркана может быть строкой или списком вариантов. Вариант выбирается
детерминированно по ключу (tg_id или пара tg_id), номеру аркана и полю — один и тот же
человек всегда видит один и тот же текст.
"""
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

ARCANA_PATH = Path(__file__).resolve().parent.parent / "content" / "arcana.json"

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


@lru_cache(maxsize=1)
def _arcana() -> dict[str, dict[str, str | list[str]]]:
    with ARCANA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def arcana(n: int) -> dict[str, str | list[str]]:
    return _arcana()[str(n)]


def variant_index(key: str, n: int, field: str, size: int) -> int:
    """Стабильный между запусками индекс варианта (встроенный hash() рандомизирован)."""
    digest = hashlib.sha256(f"{key}|{n}|{field}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % size


def user_key(tg_id: int) -> str:
    return str(tg_id)


def pair_key(tg_id_a: int, tg_id_b: int) -> str:
    """Одинаковый ключ для обоих партнёров."""
    a, b = sorted((tg_id_a, tg_id_b))
    return f"{a}:{b}"


def pick(value: str | list[str], key: str | None, n: int, field: str) -> str:
    if isinstance(value, str):
        return value
    if not value:
        return ""
    if key is None:
        return value[0]
    return value[variant_index(key, n, field, len(value))]


def name(n: int) -> str:
    return pick(arcana(n)["name"], None, n, "name")


def text(n: int, field: str, key: str | None = None) -> str:
    return pick(arcana(n)[field], key, n, field)


def first_sentence(s: str) -> str:
    return _SENTENCE_END.split(s.strip(), maxsplit=1)[0]
