"""Блок 10: карточки-картинки для сторис и команда /matrix в группах."""
import io

import pytest
from aiogram.methods import SendPhoto
from PIL import Image
from sqlalchemy import select

from bot.content import messages as msg
from bot.db.models import Event, User
from bot.services import cards, texts
from bot.services.gender import has_templates, render
from tests.conftest import buttons, sent, sent_texts

U, GROUP = 9901, -100777


async def _user(db, tg_id):
    async with db() as s:
        return await s.scalar(select(User).where(User.tg_id == tg_id))


# --- Картинки ---

@pytest.mark.parametrize("n", range(1, 23))
def test_card_renders_for_every_arcana_with_cyrillic_font(n):
    png = cards.render_card(n, texts.name(n), render(texts.text(n, "teaser"), "f"), "matrix_destiny_bot")
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and img.size == (1080, 1920)


def test_font_with_cyrillic_is_shipped_in_project():
    assert cards.FONT_REGULAR.exists() and cards.FONT_BOLD.exists()
    assert cards.FONT_REGULAR.is_relative_to(cards.ASSETS.parent.parent)


def test_card_rejects_text_that_cannot_fit():
    with pytest.raises(ValueError):
        cards.render_card(1, "Маг", "я" * 400, "bot")  # одно «слово» без пробелов шире полей


def test_card_is_deterministic():
    a = cards.render_card(3, texts.name(3), "Фраза.", "bot")
    b = cards.render_card(3, texts.name(3), "Фраза.", "bot")
    assert a == b  # без случайности и без времени на картинке


async def test_story_button_sends_png_with_referral_link(tg, db):
    await tg.send(U, "/start")
    calls = await tg.send(U, "07.03.1994")
    assert "story" in [b.callback_data for b in buttons(sent(calls)[1])]
    assert [b.text for b in buttons(sent(calls)[1])][-1] == msg.BTN_STORY

    await tg.click(U, "story")
    photo = [c for c in tg.fake.calls if isinstance(c, SendPhoto)][-1]
    img = Image.open(io.BytesIO(photo.photo.data))
    assert img.size == (1080, 1920)
    code = (await _user(db, U)).ref_code
    assert f"https://t.me/test_bot?start=r_{code}" in photo.caption
    assert not has_templates(photo.caption) and "1994" not in photo.caption
    async with db() as s:
        ev = await s.scalar(select(Event).where(Event.name == "story_card"))
    assert ev.payload == {"arcana": 7}


async def test_story_without_date_asks_for_date(tg, db):
    await tg.send(U, "/start")
    calls = await tg.click(U, "story")
    assert sent_texts(calls) == [msg.ASK_DATE]
    assert not [c for c in tg.fake.calls if isinstance(c, SendPhoto)]


# --- Группы ---

async def test_group_matrix_gives_link_button_without_asking_date(tg, db):
    calls = await tg.group_send(GROUP, U, "/matrix")
    [reply] = sent(calls)
    assert reply.chat_id == GROUP and reply.text == msg.GROUP_MATRIX
    user = await _user(db, U)
    [button] = buttons(reply)
    assert button.text == msg.BTN_GROUP_MATRIX and button.url == f"https://t.me/test_bot?start=r_{user.ref_code}"
    assert user.birth_date is None
    async with db() as s:
        assert (await s.scalar(select(Event).where(Event.name == "group_matrix"))).user_id == user.id


async def test_group_matrix_with_bot_suffix_and_cooldown(tg, db):
    assert len(sent(await tg.group_send(GROUP, U, "/matrix@test_bot"))) == 1
    assert sent(await tg.group_send(GROUP, U + 1, "/matrix")) == []  # в этом чате только что отвечали
    assert len(sent(await tg.group_send(GROUP - 1, U + 1, "/matrix"))) == 1  # другой чат — можно


async def test_group_collects_no_dates_and_creates_no_users(tg, db):
    assert await tg.group_send(GROUP, U, "07.03.1994") == []
    assert await tg.group_send(GROUP, U, "/start") == []
    assert await tg.group_send(GROUP, U, "привет всем") == []
    assert await _user(db, U) is None  # болтовня в группе не делает человека пользователем бота
    await tg.group_send(GROUP, U, "/matrix")
    assert (await _user(db, U)).birth_date is None


def test_group_commands_registered_for_all_groups():
    from bot.main import GROUP_COMMANDS
    assert [c.command for c in GROUP_COMMANDS] == ["matrix"]
