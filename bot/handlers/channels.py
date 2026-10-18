import logging
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatJoinRequest, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kb
from bot.content import messages as msg
from bot.db.models import Channel, User
from bot.handlers import matrix
from bot.services import channels as channels_svc
from bot.services import events

log = logging.getLogger(__name__)

router = Router(name="channels")


async def _mark_ok(session: AsyncSession, user: User, state: FSMContext, channels: list[Channel]) -> None:
    data = await state.get_data()
    prompted = set(data.get("missing_channels", []))
    if user.channels_ok_at is None:
        user.channels_ok_at = datetime.now(timezone.utc)
        await events.track(session, user.id, "channels_ok", {"channels": sorted(prompted)})
        for ch in channels:
            if ch.id in prompted:
                ch.joined_count += 1
    if prompted:
        await state.update_data(missing_channels=[])


def channels_prompt(missing: list[Channel]) -> str:
    if any(ch.join_mode == "request" for ch in missing):
        return msg.CHANNELS_PROMPT + msg.CHANNELS_REQUEST_HINT
    return msg.CHANNELS_PROMPT


@router.chat_join_request()
async def on_join_request(request: ChatJoinRequest, session: AsyncSession) -> None:
    channel = await session.scalar(select(Channel).where(Channel.chat_id == request.chat.id))
    if channel is None:
        return
    await channels_svc.save_join_request(session, request.from_user.id, request.chat.id)
    if channel.auto_approve:
        try:
            await request.approve()
        except TelegramAPIError as e:
            log.warning("approveChatJoinRequest failed for chat %s: %s", request.chat.id, e)


async def open_full(message: Message, bot: Bot, session: AsyncSession, user: User, state: FSMContext) -> None:
    """Полный разбор: если есть каналы и подписка не на все — просим подписаться."""
    channels = await channels_svc.active_channels(session)
    missing = await channels_svc.missing_channels(bot, session, channels, user.tg_id) if channels else []
    if missing:
        await state.update_data(missing_channels=[ch.id for ch in missing])
        await events.track(session, user.id, "channels_prompt", {"missing": [ch.id for ch in missing]})
        await message.answer(channels_prompt(missing), reply_markup=kb.channels_kb(missing))
        return
    await _mark_ok(session, user, state, channels)
    await matrix.show_full(message, session, user)


@router.callback_query(F.data == "full")
async def cb_full(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, state: FSMContext) -> None:
    await call.answer()
    if not user.birth_date:
        await matrix.ask_date(call.message, state)
        return
    await events.track(session, user.id, "full_clicked")
    if await matrix.need_gender(call.message, user, state, "full"):
        return
    await open_full(call.message, bot, session, user, state)


@router.callback_query(F.data == "check_subs")
async def cb_check_subs(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, state: FSMContext) -> None:
    if not user.birth_date:
        await call.answer()
        await matrix.ask_date(call.message, state)
        return
    if user.gender is None:
        await call.answer()
        await matrix.ask_gender(call.message, state, "full")  # после ответа open_full проверит подписку заново
        return
    channels = await channels_svc.active_channels(session)
    missing = await channels_svc.missing_channels(bot, session, channels, user.tg_id)
    if missing:
        await call.answer(msg.CHANNELS_STILL_MISSING, show_alert=True)
        return
    await call.answer()
    already_seen = user.channels_ok_at is not None  # вернулся после отписки — разбор заново не шлём
    await _mark_ok(session, user, state, channels)
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass
    if already_seen:
        await call.message.answer(msg.CHANNELS_BACK)
        await matrix.send_spheres_menu(call.message, session, user)
        return
    await matrix.show_full(call.message, session, user)
