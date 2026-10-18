"""Блок 7: inline-режим."""
from aiogram.methods import AnswerInlineQuery
from sqlalchemy import select

from bot.content import messages as msg
from bot.db.models import Event, User
from bot.handlers.admin_stats import render_virality
from bot.services.gender import has_templates
from bot.services.stats import make_period
from tests.conftest import _dp

U = 9501


async def _user(db, tg_id) -> User:
    async with db() as s:
        return await s.scalar(select(User).where(User.tg_id == tg_id))


def _answer(tg) -> AnswerInlineQuery:
    return [c for c in tg.fake.calls if isinstance(c, AnswerInlineQuery)][-1]


def test_inline_updates_are_allowed():
    used = _dp.resolve_used_update_types()
    assert "inline_query" in used and "chosen_inline_result" in used


async def test_cards_for_user_with_date(tg, db):
    await tg.send(U, "/start")
    await tg.send(U, "07.03.1994")
    code = (await _user(db, U)).ref_code
    await tg.inline(U)
    answer = _answer(tg)
    assert answer.is_personal is True
    by_id = {r.id: r for r in answer.results}
    assert list(by_id) == ["arcana", "day", "compat"]

    arcana = by_id["arcana"]
    assert arcana.title == "Мой аркан"
    text = arcana.input_message_content.message_text
    assert "Мой аркан — 7. Колесница" in text
    assert "1994" not in text and "07.03" not in text  # точной даты в карточке нет
    button = arcana.reply_markup.inline_keyboard[0][0]
    assert button.text == msg.BTN_INLINE_CALC and button.url == f"https://t.me/test_bot?start=r_{code}"

    day = by_id["day"]
    assert day.input_message_content.message_text.startswith("☀️ <b>Моё число дня — ")
    assert day.reply_markup.inline_keyboard[0][0].url == f"https://t.me/test_bot?start=r_{code}"

    compat = by_id["compat"]
    assert compat.title == "Проверить совместимость"
    assert compat.reply_markup.inline_keyboard[0][0].url == f"https://t.me/test_bot?start=c_{code}"
    for r in answer.results:
        assert not has_templates(r.input_message_content.message_text) and not has_templates(r.title)


async def test_single_variant_without_date(tg, db):
    await tg.send(U, "/start")
    await tg.inline(U)
    [r] = _answer(tg).results
    assert r.id == "need_date" and r.title == "Сначала рассчитай свою матрицу"
    assert r.reply_markup.inline_keyboard[0][0].url == "https://t.me/test_bot"


async def test_unknown_user_gets_need_date_card(tg, db):
    await tg.inline(9999)  # человек, ни разу не писавший боту
    assert [r.id for r in _answer(tg).results] == ["need_date"]
    assert (await _user(db, 9999)) is not None


async def test_inline_counts_as_activity(tg, db):
    await tg.inline(U)
    assert (await _user(db, U)).last_seen_at is not None


async def test_chosen_result_recorded_and_shown_in_virality(tg, db):
    await tg.send(U, "/start")
    await tg.send(U, "07.03.1994")
    await tg.chosen(U, "arcana")
    await tg.chosen(U, "arcana")
    await tg.chosen(U, "compat")
    async with db() as s:
        events = list(await s.scalars(select(Event.payload).where(Event.name == "inline_shared")))
        text = await render_virality(s, make_period("all"))
    assert events.count({"variant": "arcana"}) == 2 and events.count({"variant": "compat"}) == 1
    assert "Inline-шеринг (выбрали карточку): <b>3</b> (Мой аркан — 2, Проверить совместимость — 1)" in text
