"""Рассылки от админа: сегменты, отправка с ограничением скорости, итоговый отчёт.

Рассылка уважает «/stop» (notify_marketing=false) и не пишет заблокировавшим. Лимит скорости, RetryAfter и
Forbidden обрабатывает pushes.deliver. Тихие часы и суточный лимит пушей на рассылки админа не действуют:
решение о времени принимает админ.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.db.models import Broadcast, Channel, Referral, User
from bot.services import channels as channels_svc
from bot.services import pushes
from bot.services.clock import now_utc

log = logging.getLogger(__name__)

SEGMENTS = {
    "all": "Все активированные",
    "no_channels": "Не прошли каналы",
    "no_invites": "Без приглашённых",
    "girls": "Девушки",
    "boys": "Парни",
}
CHANNEL_PREFIX = "ch"  # сегмент «подписчики канала»: ключ ch<id канала в базе>
CHECK_PAUSE = 0.04  # пауза между проверками подписки (~25 запросов в секунду)
PROGRESS_EVERY = 25  # сообщений между обновлениями прогресса и коммитами
MAX_TEXT = 4096

tasks: set[asyncio.Task] = set()  # запущенные рассылки (держим ссылки, чтобы их не собрал сборщик мусора)
stop_requests: set[int] = set()


def channel_segment_id(key: str) -> int | None:
    """ch12 → 12; для обычных сегментов None."""
    if key.startswith(CHANNEL_PREFIX) and key[len(CHANNEL_PREFIX):].isdigit():
        return int(key[len(CHANNEL_PREFIX):])
    return None


async def segment_title(session: AsyncSession, key: str) -> str:
    channel_id = channel_segment_id(key)
    if channel_id is None:
        return SEGMENTS.get(key, key)
    channel = await session.get(Channel, channel_id)
    return f"Подписчики канала «{channel.title if channel else channel_id}»"


async def _subscribed(bot: Bot, channel: Channel, tg_id: int, requested: set[int]) -> bool:
    """Строгая проверка подписки: при ошибке Telegram исключение уходит наверх (в отличие от channels.is_member)."""
    if channel.chat_id in requested:
        return True
    while True:
        try:
            member = await bot.get_chat_member(channel.chat_id, tg_id)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            continue
        return member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR) or (
            member.status == ChatMemberStatus.RESTRICTED and getattr(member, "is_member", False)
        )


async def channel_member_ids(bot: Bot, session: AsyncSession, channel: Channel) -> list[int]:
    """Пользователи бота (активированные, не заблокировавшие, не отключившие рассылки), подписанные на канал.

    Telegram не отдаёт список подписчиков канала: написать можно только тем, кто сам запускал бота.
    TelegramAPIError (бот не админ канала, канал удалён) поднимается наверх — админ должен увидеть причину.
    """
    rows = (await session.execute(segment_stmt("all").with_only_columns(User.id, User.tg_id).order_by(User.id))).all()
    result = []
    for uid, tg_id in rows:
        requested = await channels_svc.requested_chats(session, tg_id)
        if await _subscribed(bot, channel, tg_id, requested):
            result.append(uid)
        await asyncio.sleep(CHECK_PAUSE)
    return result


def segment_stmt(key: str):
    stmt = select(User).where(
        User.activated_at.is_not(None), User.is_blocked.is_(False), User.notify_marketing.is_(True)
    )
    if key == "no_channels":
        stmt = stmt.where(User.channels_ok_at.is_(None))
    elif key == "no_invites":
        stmt = stmt.where(User.id.not_in(select(Referral.inviter_id)))
    elif key == "girls":
        stmt = stmt.where(User.gender == "f")
    elif key == "boys":
        stmt = stmt.where(User.gender == "m")
    elif key != "all":
        raise ValueError(key)
    return stmt


async def segment_ids(session: AsyncSession, key: str) -> list[int]:
    return list(await session.scalars(segment_stmt(key).with_only_columns(User.id).order_by(User.id)))


async def segment_size(session: AsyncSession, key: str) -> int:
    sub = segment_stmt(key).subquery()
    return await session.scalar(select(func.count()).select_from(sub)) or 0


ProgressCallback = Callable[[Broadcast], Awaitable[None]]


async def run_broadcast(
    bot: Bot,
    sessionmaker: async_sessionmaker,
    broadcast_id: int,
    on_progress: ProgressCallback | None = None,
    ids: list[int] | None = None,
) -> Broadcast:
    """Отправляет рассылку и обновляет счётчики в таблице broadcasts.

    ids — заранее найденные получатели (сегмент «подписчики канала» проверяется один раз при выборе).
    """
    async with sessionmaker() as session:
        bc = await session.get(Broadcast, broadcast_id)
        if ids is None:
            ids = await segment_ids(session, bc.segment)
        bc.status, bc.total, bc.started_at = "running", len(ids), now_utc()
        await session.commit()
        try:
            for i, uid in enumerate(ids, 1):
                if broadcast_id in stop_requests:
                    bc.status = "canceled"
                    break
                user = await session.get(User, uid)
                if user is None or user.is_blocked or not user.notify_marketing:
                    continue
                result = await pushes.deliver(bot, user, bc.text)
                if result == "sent":
                    bc.sent += 1
                elif result == "blocked":
                    bc.blocked += 1
                else:
                    bc.failed += 1
                if i % PROGRESS_EVERY == 0:
                    await session.commit()
                    if on_progress:
                        await on_progress(bc)
            else:
                bc.status = "done"
        except Exception:  # noqa: BLE001
            log.exception("broadcast %s failed", broadcast_id)
            bc.status = "failed"
        finally:
            stop_requests.discard(broadcast_id)
            bc.finished_at = now_utc()
            await session.commit()
    return bc


def report(bc: Broadcast) -> str:
    title = {"done": "✅ Рассылка завершена", "canceled": "⏹ Рассылка остановлена", "failed": "⚠️ Рассылка прервана ошибкой"}
    return (
        f"{title.get(bc.status, '📤 Рассылка идёт')} (#{bc.id})\n"
        f"Получателей: {bc.total}\nОтправлено: {bc.sent}\nЗаблокировали бота: {bc.blocked}\nОшибки: {bc.failed}"
    )


def progress(bc: Broadcast) -> str:
    return f"📤 Рассылка #{bc.id}: {bc.sent + bc.blocked + bc.failed} из {bc.total}…"


