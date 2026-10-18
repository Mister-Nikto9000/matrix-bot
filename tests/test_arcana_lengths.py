"""Объём и уникальность расширенных текстов арканов (блок 1)."""
import re

from bot.services import texts
from bot.services.gender import render

RANGES = {"personality": (650, 750), "talents": (650, 750), "purpose": (700, 800), "comfort": (350, 450)}
_SENTENCE = re.compile(r"(?<=[.!?…])\s+")


def test_lengths_in_target_ranges_for_both_genders():
    for n in range(1, 23):
        for field, (lo, hi) in RANGES.items():
            for gender in ("f", "m"):
                length = len(render(texts.text(n, field), gender))
                assert lo <= length <= hi, (n, field, gender, length)


def test_no_identical_sentences_between_arcana():
    owner: dict[str, int] = {}
    for n in range(1, 23):
        for field in RANGES:
            for sentence in _SENTENCE.split(render(texts.text(n, field), "f")):
                key = sentence.strip().lower()
                assert owner.setdefault(key, n) == n, (n, owner[key], sentence[:60])


def test_purpose_first_sentence_is_standalone():
    """Первое предложение purpose идёт в тизер при повторе арканов — оно должно быть законченной мыслью."""
    for n in range(1, 23):
        first = texts.first_sentence(render(texts.text(n, "purpose"), "f"))
        assert 30 <= len(first) <= 200 and first[-1] in ".!?…", (n, first)


def test_fields_do_not_start_the_same_way():
    for field in ("talents", "purpose", "comfort"):
        starts = [" ".join(render(texts.text(n, field), "f").split()[:2]) for n in range(1, 23)]
        assert len(set(starts)) >= 20, (field, starts)
