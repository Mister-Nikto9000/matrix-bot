"""Рассылки от админа: сегменты, отправка с ограничением скорости, итоговый отчёт.

Рассылка уважает «/stop» (notify_marketing=false) и не пишет заблокировавшим. Лимит скорости, RetryAfter и
Forbidden обрабатывает pushes.deliver. Тихие часы и суточный лимит пушей на рассылки админа не действуют:
решение о времени принимает админ.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.db.models import Broadcast, Referral, User
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
PROGRESS_EVERY = 25  # сообщений между обновлениями прогресса и коммитами
MAX_TEXT = 4096

tasks: set[asyncio.Task] = set()  # запущенные рассылки (держим ссылки, чтобы их не собрал сборщик мусора)
stop_requests: set[int] = set()


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
    bot: Bot, sessionmaker: async_sessionmaker, broadcast_id: int, on_progress: ProgressCallback | None = None
) -> Broadcast:
    """Отправляет рассылку и обновляет счётчики в таблице broadcasts."""
    async with sessionmaker() as session:
        bc = await session.get(Broadcast, broadcast_id)
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


