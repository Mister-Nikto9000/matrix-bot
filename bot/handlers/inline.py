"""Inline-режим: @бот в любом чате → карточки «Мой аркан», «Число дня», «Проверить совместимость».

В карточки попадают только номер и название аркана и тексты арканов — без даты рождения. Выбор варианта
(chosen_inline_result) пишется событием inline_shared; для этого у бота в @BotFather нужно включить
/setinlinefeedback (100%).
"""
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.types import (
    ChosenInlineResult,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.content import messages as msg
from bot.db.models import User
from bot.handlers.invite import compat_link, ref_link
from bot.services import events, texts
from bot.services.clock import msk_today
from bot.services.daily import day_text
from bot.services.gender import render
from bot.services.matrix_calc import calculate

router = Router(name="inline")

VARIANTS = ("arcana", "day", "compat", "need_date")


def _article(result_id: str, title: str, description: str, text: str, button: str, url: str, gender: str | None):
    return InlineQueryResultArticle(
        id=result_id,
        title=render(title, gender),
        description=render(description, gender),
        input_message_content=InputTextMessageContent(message_text=render(text, gender), parse_mode=ParseMode.HTML),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=render(button, gender), url=url)]]),
    )


def build_results(user: User) -> list[InlineQueryResultArticle]:
    gender = user.gender
    if user.birth_date is None:
        bot_link = f"https://t.me/{get_settings().bot_username}"
        return [_article("need_date", msg.INLINE_NEED_DATE_TITLE, msg.INLINE_NEED_DATE_DESC, msg.INLINE_NEED_DATE_TEXT,
                         msg.BTN_INLINE_OPEN_BOT, bot_link, gender)]
    m = calculate(user.birth_date)
    key = texts.user_key(user.tg_id)
    num, day_message = day_text(user, msk_today())
    day_body = day_message.split("\n\n", 1)[1]  # без заголовка: он есть в шаблоне карточки
    arcana = msg.INLINE_ARCANA_TEXT.format(num=m.a, name=texts.name(m.a), teaser=texts.text(m.a, "teaser", key))
    day = msg.INLINE_DAY_TEXT.format(num=num, name=texts.name(num), text=day_body)
    return [
        _article("arcana", msg.INLINE_ARCANA_TITLE, msg.INLINE_ARCANA_DESC, arcana, msg.BTN_INLINE_CALC, ref_link(user), gender),
        _article("day", msg.INLINE_DAY_TITLE, msg.INLINE_DAY_DESC, day, msg.BTN_INLINE_CALC, ref_link(user), gender),
        _article("compat", msg.INLINE_COMPAT_TITLE, msg.INLINE_COMPAT_DESC, msg.INLINE_COMPAT_TEXT,
                 msg.BTN_INLINE_COMPAT, compat_link(user), gender),
    ]


@router.inline_query()
async def on_inline_query(query: InlineQuery, user: User) -> None:
    # is_personal: у каждого свои карточки; короткий кэш, чтобы набор текста не создавал лишних расчётов
    await query.answer(build_results(user), is_personal=True, cache_time=5)


@router.chosen_inline_result()
async def on_chosen_inline_result(result: ChosenInlineResult, session: AsyncSession, user: User) -> None:
    await events.track(session, user.id, "inline_shared", {"variant": result.result_id})
