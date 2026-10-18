"""Подключение каналов без ввода ID и ссылок: бот сам замечает, что его сделали админом,
или админ пересылает пост из канала. Управление — кнопками под /channels."""
import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatType, MessageOriginType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kb
from bot.config import get_settings
from bot.content import messages as msg
from bot.db.models import Channel, JoinRequest
from bot.handlers.admin import IsAdmin
from bot.services import channels as channels_svc
from bot.services.notify import notify_admins
from bot.services.channels import ADMIN_STATUSES, JOIN_MODES

log = logging.getLogger(__name__)

router = Router(name="channel_admin")
# my_chat_member не фильтруем: бота может добавить любой админ канала, а пишем всё равно ADMIN_IDS
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def _offer(session: AsyncSession, chat_id: int, title: str, intro: str) -> tuple[str, InlineKeyboardMarkup]:
    """Текст и кнопки предложения подключить канал; для подключённого — текущие настройки."""
    channel = await session.scalar(select(Channel).where(Channel.chat_id == chat_id))
    if channel is None:
        return intro.format(title=escape(title)), kb.channel_offer_kb(chat_id, connected=False)
    text = msg.ADMIN_CHANNEL_ALREADY.format(
        title=escape(channel.title), mode=msg.ADMIN_JOIN_MODES[channel.join_mode],
        state="активен" if channel.is_active else "выключен", url=escape(channel.url),
    )
    return text, kb.channel_offer_kb(chat_id, connected=True)


@router.my_chat_member(F.chat.type == ChatType.CHANNEL)
async def on_my_chat_member(update: ChatMemberUpdated, bot: Bot, session: AsyncSession) -> None:
    was_admin = update.old_chat_member.status in ADMIN_STATUSES
    is_admin = update.new_chat_member.status in ADMIN_STATUSES
    title = update.chat.title or str(update.chat.id)
    if is_admin and not was_admin:
        text, markup = await _offer(session, update.chat.id, title, msg.ADMIN_CHANNEL_OFFER)
        await notify_admins(bot, text, markup)
    elif was_admin and not is_admin:
        channel = await session.scalar(select(Channel).where(Channel.chat_id == update.chat.id))
        if channel is not None and channel.is_active:
            channel.is_active, channel.title = False, title
            await notify_admins(bot, msg.ADMIN_CHANNEL_REMOVED.format(title=escape(title)))


@router.message(F.forward_origin.type == MessageOriginType.CHANNEL)
async def on_forward(message: Message, bot: Bot, session: AsyncSession) -> None:
    chat = message.forward_origin.chat
    title = chat.title or str(chat.id)
    is_admin, _ = await channels_svc.bot_rights(bot, chat.id)
    if not is_admin:
        await message.answer(msg.ADMIN_FORWARD_NOT_ADMIN.format(title=escape(title)))
        return
    text, markup = await _offer(session, chat.id, title, msg.ADMIN_CHANNEL_OFFER_FORWARD)
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


async def _chat_title(bot: Bot, session: AsyncSession, chat_id: int) -> str:
    try:
        chat = await bot.get_chat(chat_id)
        if chat.title:
            return chat.title
    except TelegramAPIError:
        pass
    channel = await session.scalar(select(Channel).where(Channel.chat_id == chat_id))
    return channel.title if channel else str(chat_id)


async def _drop_markup(call: CallbackQuery) -> None:
    try:
        await call.message.edit_reply_markup(reply_markup=None)  # чтобы не нажимали повторно
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("chadd:"))
async def cb_connect(call: CallbackQuery, bot: Bot, session: AsyncSession) -> None:
    _, mode, raw = call.data.split(":", 2)
    await call.answer()
    if mode == "skip":
        await _drop_markup(call)
        await call.message.answer(msg.ADMIN_CHANNEL_SKIPPED)
        return
    if mode not in JOIN_MODES or not raw.lstrip("-").isdigit():
        return
    chat_id = int(raw)
    title = await _chat_title(bot, session, chat_id)
    is_admin, can_invite = await channels_svc.bot_rights(bot, chat_id)
    if not is_admin:
        await call.message.answer(msg.ADMIN_FORWARD_NOT_ADMIN.format(title=escape(title)))
        return
    # Кнопки не убираем: после выдачи права админ нажмёт ещё раз
    url = await channels_svc.create_invite_link(bot, chat_id, mode) if can_invite else None
    if url is None:
        await call.message.answer(msg.ADMIN_NO_INVITE_RIGHT.format(title=escape(title)))
        return
    await channels_svc.connect(session, chat_id, title, url, mode)
    await _drop_markup(call)
    await call.message.answer(
        msg.ADMIN_CHANNEL_CONNECTED.format(title=escape(title), mode=msg.ADMIN_JOIN_MODES[mode], url=escape(url)),
        disable_web_page_preview=True,
    )


# --- /channels ---

async def _channel_text(session: AsyncSession, ch: Channel) -> str:
    if ch.join_mode == "request":
        requests = await session.scalar(
            select(func.count()).select_from(JoinRequest).where(JoinRequest.chat_id == ch.chat_id)
        )
        mode = msg.ADMIN_MODE_REQUEST.format(requests=requests or 0, auto="вкл" if ch.auto_approve else "выкл")
    else:
        mode = msg.ADMIN_MODE_PUBLIC
    return msg.ADMIN_CHANNEL_LINE.format(
        id=ch.id, title=escape(ch.title), url=escape(ch.url),
        state="активен" if ch.is_active else "выключен", mode=mode, joined=ch.joined_count,
    )


@router.message(Command("channels"))
async def cmd_channels(message: Message, session: AsyncSession) -> None:
    rows = await channels_svc.all_channels(session)
    if not rows:
        await message.answer(msg.ADMIN_CHANNELS_EMPTY)
    for ch in rows:
        await message.answer(
            await _channel_text(session, ch), reply_markup=kb.channel_manage_kb(ch), disable_web_page_preview=True
        )
    await message.answer(msg.ADMIN_CHANNELS_HINT)


async def _edit(call: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None) -> None:
    try:
        await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except TelegramBadRequest:  # message is not modified
        pass


@router.callback_query(F.data.startswith("ch:"))
async def cb_manage(call: CallbackQuery, bot: Bot, session: AsyncSession) -> None:
    _, action, raw = call.data.split(":", 2)
    channel = await session.get(Channel, int(raw)) if raw.isdigit() else None
    if channel is None:
        await call.answer(msg.ADMIN_CHANNEL_MISSING.format(id=raw), show_alert=True)
        await _drop_markup(call)
        return

    note = None
    if action == "toggle":
        channel.is_active = not channel.is_active
    elif action == "mode":
        mode = "public" if channel.join_mode == "request" else "request"
        _, can_invite = await channels_svc.bot_rights(bot, channel.chat_id)
        url = await channels_svc.create_invite_link(bot, channel.chat_id, mode) if can_invite else None
        if url is None:
            await call.answer(msg.ADMIN_NO_INVITE_RIGHT_ALERT, show_alert=True)
            return
        channel.join_mode, channel.url = mode, url
    elif action == "auto":
        if channel.join_mode != "request":
            await call.answer(msg.ADMIN_AUTO_ONLY_REQUEST, show_alert=True)
            return
        channel.auto_approve = not channel.auto_approve
    elif action in ("up", "down"):
        moved = await channels_svc.move(session, channel, -1 if action == "up" else 1)
        if moved is None:
            await call.answer(msg.ADMIN_CHANNEL_EDGE)
            return
        note = msg.ADMIN_CHANNEL_MOVED.format(pos=moved[0], total=moved[1])
    elif action == "del":
        await call.answer()
        await _edit(call, msg.ADMIN_CHANNEL_DELETE_CONFIRM.format(title=escape(channel.title)),
                    kb.channel_delete_kb(channel))
        return
    elif action == "delyes":
        title = channel.title
        await session.delete(channel)
        await call.answer()
        await _edit(call, msg.ADMIN_CHANNEL_DELETED_TITLE.format(title=escape(title)), None)
        return
    elif action != "delno":
        await call.answer()
        return

    await session.flush()
    await call.answer(note)
    await _edit(call, await _channel_text(session, channel), kb.channel_manage_kb(channel))
