"""Служебные значения в таблице bot_state (настройки дайджеста, время алертов и бэкапа)."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import BotState


async def get(session: AsyncSession, key: str) -> str | None:
    row = await session.get(BotState, key)
    return row.value if row else None


async def set_value(session: AsyncSession, key: str, value: str | None) -> None:
    row = await session.get(BotState, key)
    if row is None:
        session.add(BotState(key=key, value=value))
    else:
        row.value = value
    await session.flush()


async def prefixed(session: AsyncSession, prefix: str) -> dict[str, str | None]:
    rows = await session.scalars(select(BotState).where(BotState.key.like(f"{prefix}%")))
    return {r.key: r.value for r in rows}
