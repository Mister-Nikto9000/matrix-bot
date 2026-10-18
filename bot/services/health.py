"""Состояние бота для /health."""
import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Channel, User
from bot.services import backup, state
from bot.services.clock import now_utc

_started = time.monotonic()
last_update_at: datetime | None = None


def mark_started() -> None:
    global _started
    _started = time.monotonic()


def touch_update() -> None:
    """Вызывается на каждом входящем апдейте."""
    global last_update_at
    last_update_at = now_utc()


def uptime_seconds() -> float:
    return time.monotonic() - _started


def human_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    return (f"{days} д " if days else "") + f"{hours} ч {minutes} мин"


def human_size(n: int) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} Б"


async def db_size(session: AsyncSession) -> int | None:
    """Размер базы в байтах: файл SQLite или pg_database_size для PostgreSQL."""
    bind = session.bind  # реальный движок сессии, а не настройка из .env
    if bind.dialect.name == "sqlite":
        path = backup.sqlite_path(bind.url.render_as_string(hide_password=False))
        return path.stat().st_size if path is not None and path.exists() else None
    if bind.dialect.name == "postgresql":
        return await session.scalar(text("SELECT pg_database_size(current_database())"))
    return None


@dataclass
class Health:
    uptime: float
    db_size: int | None
    last_update: datetime | None
    active_channels: int
    users: int
    last_backup: str | None
    deleted_data: int


async def collect(session: AsyncSession) -> Health:
    return Health(
        uptime=uptime_seconds(),
        db_size=await db_size(session),
        last_update=last_update_at,
        active_channels=await session.scalar(select(func.count()).select_from(Channel).where(Channel.is_active.is_(True))) or 0,
        users=await session.scalar(select(func.count()).select_from(User)) or 0,
        last_backup=await state.get(session, "last_backup"),
        deleted_data=int(await state.get(session, "deleted_data_count") or 0),
    )
