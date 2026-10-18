"""Что открыто у пользователя."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Unlock
from bot.services import events


async def get_unlocked(session: AsyncSession, user_id: int) -> set[str]:
    rows = await session.scalars(select(Unlock.item).where(Unlock.user_id == user_id))
    return set(rows)


async def is_unlocked(session: AsyncSession, user_id: int, item: str) -> bool:
    return item in await get_unlocked(session, user_id)


async def grant(session: AsyncSession, user_id: int, item: str, source: str) -> bool:
    """Открыть пункт. True — если открыт впервые."""
    if await session.scalar(select(Unlock.id).where(Unlock.user_id == user_id, Unlock.item == item)):
        return False
    session.add(Unlock(user_id=user_id, item=item, source=source))
    await session.flush()
    await events.track(session, user_id, "unlock", {"item": item, "source": source})
    return True
