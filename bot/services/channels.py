"""Проверка подписки на каналы: заявка на вступление или getChatMember + кэш в памяти на 10 минут.

Кэшируются только положительные ответы — иначе «Проверить подписку» не сработал бы до истечения кэша.
Для закрытых каналов (вступление по заявке) getChatMember до одобрения отдаёт left,
поэтому поданная заявка (таблица join_requests) засчитывается как подписка.
"""
import logging
import time

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import CHANNEL_CACHE_TTL
from bot.db.models import Channel, JoinRequest

log = logging.getLogger(__name__)

_cache: dict[tuple[int, int], float] = {}  # (chat_id, tg_id) -> время истечения


async def active_channels(session: AsyncSession) -> list[Channel]:
    rows = await session.scalars(
        select(Channel).where(Channel.is_active.is_(True)).order_by(Channel.sort_order, Channel.id)
    )
    return list(rows)


async def is_member(bot: Bot, chat_id: int, tg_id: int) -> bool:
    key = (chat_id, tg_id)
    if _cache.get(key, 0) > time.monotonic():
        return True
    try:
        member = await bot.get_chat_member(chat_id, tg_id)
    except TelegramAPIError as e:
        # Канал настроен неверно (бот не админ, канал удалён) — не блокируем пользователя
        log.warning("getChatMember failed for chat %s: %s", chat_id, e)
        return True
    ok = member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR) or (
        member.status == ChatMemberStatus.RESTRICTED and getattr(member, "is_member", False)
    )
    if ok:
        _cache[key] = time.monotonic() + CHANNEL_CACHE_TTL
    return ok


async def requested_chats(session: AsyncSession, tg_id: int) -> set[int]:
    rows = await session.scalars(select(JoinRequest.chat_id).where(JoinRequest.tg_id == tg_id))
    return set(rows)


async def missing_channels(bot: Bot, session: AsyncSession, channels: list[Channel], tg_id: int) -> list[Channel]:
    requested = await requested_chats(session, tg_id) if channels else set()
    return [ch for ch in channels if ch.chat_id not in requested and not await is_member(bot, ch.chat_id, tg_id)]


async def save_join_request(session: AsyncSession, tg_id: int, chat_id: int) -> bool:
    """Запоминает заявку. False — такая заявка уже была."""
    exists = await session.scalar(
        select(JoinRequest.id).where(JoinRequest.tg_id == tg_id, JoinRequest.chat_id == chat_id)
    )
    if exists:
        return False
    session.add(JoinRequest(tg_id=tg_id, chat_id=chat_id))
    return True


# --- Подключение каналов админом ---

ADMIN_STATUSES = (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
JOIN_MODES = ("public", "request")
INVITE_LINK_NAME = "Матрица Судьбы"


async def bot_rights(bot: Bot, chat_id: int) -> tuple[bool, bool]:
    """(бот — админ канала, может создавать пригласительные ссылки)."""
    try:
        me = await bot.get_chat_member(chat_id, bot.id)
    except TelegramAPIError:
        return False, False
    is_admin = me.status in ADMIN_STATUSES
    can_invite = me.status == ChatMemberStatus.CREATOR or bool(getattr(me, "can_invite_users", False))
    return is_admin, is_admin and can_invite


async def create_invite_link(bot: Bot, chat_id: int, join_mode: str) -> str | None:
    """Ссылка для входа: с заявкой или без. None — не хватило прав."""
    try:
        link = await bot.create_chat_invite_link(
            chat_id, name=INVITE_LINK_NAME, creates_join_request=join_mode == "request"
        )
    except TelegramAPIError as e:
        log.warning("createChatInviteLink failed for chat %s: %s", chat_id, e)
        return None
    return link.invite_link


async def connect(session: AsyncSession, chat_id: int, title: str, url: str, join_mode: str) -> Channel:
    """Подключает канал или меняет режим уже подключённого (и включает его)."""
    channel = await session.scalar(select(Channel).where(Channel.chat_id == chat_id))
    if channel is None:
        sort_order = (await session.scalar(select(func.max(Channel.sort_order))) or 0) + 1
        channel = Channel(chat_id=chat_id, sort_order=sort_order, joined_count=0, auto_approve=False)
        session.add(channel)
    channel.title, channel.url, channel.join_mode, channel.is_active = title, url, join_mode, True
    await session.flush()
    return channel


async def all_channels(session: AsyncSession) -> list[Channel]:
    return list(await session.scalars(select(Channel).order_by(Channel.sort_order, Channel.id)))


async def move(session: AsyncSession, channel: Channel, delta: int) -> tuple[int, int] | None:
    """Сдвигает канал на delta позиций. Возвращает (новая позиция с 1, всего) или None, если некуда."""
    rows = await all_channels(session)
    i = rows.index(channel)
    j = i + delta
    if not 0 <= j < len(rows):
        return None
    rows[i], rows[j] = rows[j], rows[i]
    for n, ch in enumerate(rows, 1):
        ch.sort_order = n
    return j + 1, len(rows)
