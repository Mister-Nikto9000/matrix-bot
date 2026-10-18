"""Карточка-картинка для сторис (кнопка под тизером)."""
import asyncio

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.content import messages as msg
from bot.db.models import User
from bot.handlers import matrix
from bot.handlers.invite import ref_link
from bot.services import cards, events, texts
from bot.services.gender import render
from bot.services.matrix_calc import calculate

router = Router(name="story")


@router.callback_query(F.data == "story")
async def cb_story(call: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    await call.answer()
    if not user.birth_date:
        await matrix.ask_date(call.message, state)
        return
    if await matrix.need_gender(call.message, user, state, "teaser"):
        return
    m = calculate(user.birth_date)
    phrase = render(texts.text(m.a, "teaser", texts.user_key(user.tg_id)), user.gender)
    # Pillow — CPU-bound: рисуем в потоке, чтобы не задерживать остальные апдейты
    png = await asyncio.to_thread(cards.render_card, m.a, texts.name(m.a), phrase, get_settings().bot_username)
    await call.message.answer_photo(
        BufferedInputFile(png, filename=f"matrix-{m.a}.png"),
        caption=msg.STORY_CAPTION.format(link=ref_link(user)),
    )
    await events.track(session, user.id, "story_card", {"arcana": m.a})
