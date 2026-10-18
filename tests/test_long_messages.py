"""Длинные тексты: разбиение по абзацам и два сообщения полного разбора."""
from aiogram.methods import SendChatAction

from bot.handlers.matrix import _block
from bot.services import texts
from bot.services.gender import render
from bot.services.sending import CHUNK_LIMIT, TELEGRAM_LIMIT, split_text
from tests.conftest import sent_texts

U = 7001
FIELDS = ["personality", "talents", "purpose", "comfort"]


def test_short_text_is_one_chunk():
    assert split_text("привет") == ["привет"]


def test_split_by_paragraphs_under_limit():
    paras = ["а" * 1500 for _ in range(5)]
    chunks = split_text("\n\n".join(paras))
    assert all(len(c) <= CHUNK_LIMIT for c in chunks)
    assert len(chunks) == 3  # 2 + 2 + 1 абзаца
    assert "\n\n".join(chunks) == "\n\n".join(paras)  # абзацы не потеряны и не разорваны


def test_long_paragraph_split_by_sentences():
    text = " ".join(f"Предложение номер {i}." for i in range(400))
    chunks = split_text(text, limit=500)
    assert len(chunks) > 1 and all(len(c) <= 500 for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_unbreakable_text_is_cut_hard():
    chunks = split_text("я" * 10000, limit=3800)
    assert [len(c) for c in chunks] == [3800, 3800, 2400]


def test_longest_texts_of_every_field_fit():
    """Самые длинные аркан-тексты по каждому полю, обе формы, влезают в сообщение вместе с оформлением."""
    for field in FIELDS:
        longest = max(range(1, 23), key=lambda n: max(len(render(texts.text(n, field), g)) for g in "fm"))
        for gender in "fm":
            block = render(_block("🧭", "Предназначение", longest, texts.text(longest, field)), gender)
            assert len(block) < CHUNK_LIMIT < TELEGRAM_LIMIT


def test_both_full_messages_fit_for_every_arcana():
    """Для любой комбинации арканов оба сообщения разбора короче лимита (берём худшие арканы по полям)."""
    def worst(field):
        return max(range(1, 23), key=lambda n: len(render(texts.text(n, field), "f")))

    first = "\n\n".join(_block("x", "Личность", n, texts.text(n, f)) for n, f in ((worst("personality"), "personality"), (worst("talents"), "talents")))
    second = "\n\n".join(_block("x", "Предназначение", n, texts.text(n, f)) for n, f in ((worst("purpose"), "purpose"), (worst("comfort"), "comfort")))
    assert len(first) + 200 < TELEGRAM_LIMIT and len(second) + 200 < TELEGRAM_LIMIT


async def test_full_is_two_messages_with_typing_between(tg):
    await tg.send(U, "/start")
    await tg.send(U, "07.03.1994")
    start = len(tg.fake.calls)
    await tg.click(U, "full")
    calls = tg.fake.calls[start:]
    kinds = [type(c).__name__ for c in calls if type(c).__name__ in ("SendMessage", "SendChatAction")]
    assert kinds == ["SendMessage", "SendChatAction", "SendMessage", "SendMessage"]  # разбор 1, typing, разбор 2, меню
    typing = [c for c in calls if isinstance(c, SendChatAction)][0]
    assert typing.action == "typing"
    first, second, menu = sent_texts(calls)
    assert "Личность" in first and "Таланты" in first and "Предназначение" not in first
    assert "Предназначение" in second and "Зона комфорта" in second
    assert "Сферы матрицы" in menu
    assert all(len(t) < TELEGRAM_LIMIT for t in (first, second, menu))
