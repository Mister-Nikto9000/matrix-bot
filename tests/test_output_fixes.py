"""Выдача без дублей и с персонализацией (блок 3)."""
from datetime import date

from bot.content import messages as msg
from bot.handlers.matrix import render_sphere, teaser_blocks
from bot.services import texts
from bot.services.matrix_calc import calculate
from tests.conftest import sent_texts

U = 1001


def test_teaser_repeated_arcana_not_duplicated():
    m = calculate(date(1994, 3, 7))  # таланты = предназначение = 3
    assert m.b == m.purpose == 3
    talents, purpose = teaser_blocks(m, "1")[1:]
    assert texts.text(3, "teaser") in talents
    assert texts.text(3, "teaser") not in purpose
    assert texts.first_sentence(texts.text(3, "purpose")) in purpose


def test_teaser_three_same_arcana_all_different():
    m = calculate(date(1950, 5, 5))
    assert m.a == m.b == m.purpose
    bodies = [b.split("\n", 1)[1] for b in teaser_blocks(m, "1")]
    assert len(set(bodies)) == 3


def test_first_sentence():
    assert texts.first_sentence("Раз. Два! Три?") == "Раз."
    assert texts.first_sentence("Без точки") == "Без точки"


def test_money_single_block_when_energies_match():
    m = calculate(date(1958, 1, 3))
    assert m.c == m.money_channel
    out = render_sphere("money", m)
    assert out.count(texts.text(m.c, "money")) == 1
    assert "Денежная энергия и денежный канал" in out


def test_money_two_blocks_when_different():
    m = calculate(date(1994, 3, 7))
    out = render_sphere("money", m)
    assert texts.text(m.c, "money") in out and texts.text(m.money_channel, "money") in out


def test_relationships_single_block_when_energies_match():
    m = calculate(date(2000, 1, 6))
    assert m.h == m.relationships_channel
    out = render_sphere("relationships", m)
    assert out.count(texts.text(m.h, "relationships")) == 1
    assert "Энергия и канал отношений" in out


async def test_teaser_and_full_use_first_name(tg):
    await tg.send(U, "/start", first_name="Анна")
    calls = await tg.send(U, "07.03.1994", first_name="Анна")
    assert sent_texts(calls)[1].startswith("✨ <b>Анна, вот твоя Матрица Судьбы</b>")
    calls = await tg.click(U, "full", first_name="Анна")
    assert sent_texts(calls)[0].startswith("📖 <b>Анна, вот твой полный разбор</b>")


async def test_name_is_escaped_and_optional(tg):
    await tg.send(U, "/start", first_name="<b>")
    calls = await tg.send(U, "07.03.1994", first_name="<b>")
    assert sent_texts(calls)[1].startswith("✨ <b>&lt;b&gt;, вот твоя")
    await tg.send(U + 1, "/start", first_name=" ")
    calls = await tg.send(U + 1, "07.03.1994", first_name=" ")
    assert sent_texts(calls)[1].startswith(msg.TEASER_TITLE)
