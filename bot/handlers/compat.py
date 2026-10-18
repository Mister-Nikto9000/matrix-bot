from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kb
from bot.content import messages as msg
from bot.db.models import CompatPair, User
from bot.handlers import matrix
from bot.handlers.invite import compat_link
from bot.services import events, texts, unlocks, users
from bot.services.gender import render
from bot.services.matrix_calc import CompatMatrix, calculate, compatibility

router = Router(name="compat")

FULL_ITEM = "compat_full_and_year"


def render_compat(partner_name: str, cm: CompatMatrix, detailed: bool, key: str | None = None) -> str:
    text = msg.COMPAT_BRIEF.format(
        name=escape(partner_name), num=cm.main, arcana=texts.name(cm.main), text=texts.text(cm.main, "compat", key)
    )
    if not detailed:
        return text + msg.COMPAT_DETAIL_LOCKED
    lines = [
        msg.COMPAT_DETAIL_LINE.format(label=label, num=getattr(cm, key), name=texts.name(getattr(cm, key)))
        for key, label in msg.COMPAT_POINT_LABELS.items()
    ]
    return text + msg.COMPAT_DETAIL_TITLE + "\n" + "\n".join(lines)


async def run_pair(bot: Bot, session: AsyncSession, inviter: User, partner: User, partner_message: Message) -> None:
    """partner пришёл по ссылке c_<code> пользователя inviter и уже указал дату."""
    if inviter.birth_date is None:
        # «указал{|а}» — про пригласившего, поэтому род берём его
        text = render(msg.COMPAT_PARTNER_NO_DATE, inviter.gender)
        await partner_message.answer(text.format(name=escape(users.display_name(inviter))))
        return

    user_a, user_b = sorted((inviter.id, partner.id))
    exists = await session.scalar(
        select(CompatPair.id).where(CompatPair.user_a == user_a, CompatPair.user_b == user_b)
    )
    if not exists:
        session.add(CompatPair(user_a=user_a, user_b=user_b))

    cm = compatibility(calculate(inviter.birth_date), calculate(partner.birth_date))
    key = texts.pair_key(inviter.tg_id, partner.tg_id)  # оба партнёра видят один вариант текста
    partner_full = await unlocks.is_unlocked(session, partner.id, FULL_ITEM)
    inviter_full = await unlocks.is_unlocked(session, inviter.id, FULL_ITEM)

    await partner_message.answer(render_compat(users.display_name(inviter), cm, partner_full, key))
    await partner_message.answer(msg.COMPAT_TRY_ANOTHER, reply_markup=kb.compat_another_kb())
    await users.safe_send(bot, inviter, render_compat(users.display_name(partner), cm, inviter_full, key))


async def send_compat_link(message: Message, session: AsyncSession, user: User, state: FSMContext) -> None:
    if not user.birth_date:
        await matrix.ask_date(message, state)
        return
    await message.answer(
        msg.COMPAT_LINK.format(text=msg.SHARE_COMPAT_TEXT, link=compat_link(user)),
        reply_markup=kb.share_cb_kb("c"),
        disable_web_page_preview=True,
    )
    await events.track(session, user.id, "compat_link_created")


@router.message(Command("compat"))
async def cmd_compat(message: Message, session: AsyncSession, user: User, state: FSMContext) -> None:
    await send_compat_link(message, session, user, state)


@router.callback_query(F.data == "compat")
async def cb_compat(call: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    await call.answer()
    await send_compat_link(call.message, session, user, state)
