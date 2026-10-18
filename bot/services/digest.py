"""Утренний дайджест админам: вчерашние цифры и разница с позавчера."""
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import get_settings
from bot.services import state, stats
from bot.services.clock import msk_today, now_utc
from bot.services.notify import notify_admins

log = logging.getLogger(__name__)

DIGEST_HOUR = 9  # МСК
LAST_SENT_KEY = "digest_last_sent"


def _off_key(tg_id: int) -> str:
    return f"digest_off:{tg_id}"


async def is_enabled(session: AsyncSession, tg_id: int) -> bool:
    return await state.get(session, _off_key(tg_id)) != "1"


async def toggle(session: AsyncSession, tg_id: int) -> bool:
    """Переключает дайджест, возвращает новое состояние (True — включён)."""
    enabled = await is_enabled(session, tg_id)
    await state.set_value(session, _off_key(tg_id), "1" if enabled else "0")
    return not enabled


def _int(v: int, prev: int) -> str:
    return f"{v} ({v - prev:+d})"


def _share(v: float, prev: float) -> str:
    return f"{v:.0%} ({(v - prev) * 100:+.0f} п.п.)"


async def build_text(session: AsyncSession, now: datetime | None = None) -> str:
    today = msk_today(now)
    yesterday, before = today - timedelta(days=1), today - timedelta(days=2)
    y, b = await stats.day_digest(session, yesterday), await stats.day_digest(session, before)
    d1_yesterday = await stats.cohort_d1(session, yesterday, now)  # вернулись сегодня — день ещё идёт
    d1_before = await stats.cohort_d1(session, before, now)  # вернулись вчера
    return "\n".join([
        f"☀️ <b>Дайджест за {yesterday:%d.%m}</b> <i>(в скобках — разница с {before:%d.%m})</i>",
        f"Новые: <b>{_int(y.new, b.new)}</b>",
        f"Активированные: <b>{_int(y.activated, b.activated)}</b>",
        f"K: <b>{y.k:.2f}</b> ({y.k - b.k:+.2f})",
        f"Доля реферальных: <b>{_share(y.ref_share, b.ref_share)}</b>",
        f"Прошли каналы: <b>{_int(y.channels_ok, b.channels_ok)}</b>",
        f"Ошибки: <b>{_int(y.errors, b.errors)}</b>",
        f"D1 когорты {yesterday:%d.%m} (вернулись сегодня, день ещё идёт): <b>{stats.pct_of(d1_yesterday)}</b>",
        f"D1 когорты {before:%d.%m} (вернулись вчера): <b>{stats.pct_of(d1_before)}</b>",
    ])


async def send_digest(bot: Bot, sessionmaker: async_sessionmaker, now: datetime | None = None) -> bool:
    """Отправляет дайджест, если сегодня ещё не отправляли. True — отправлен."""
    now = now or now_utc()
    today = msk_today(now).isoformat()
    async with sessionmaker() as session:
        if await state.get(session, LAST_SENT_KEY) == today:
            return False
        await state.set_value(session, LAST_SENT_KEY, today)
        recipients = [a for a in get_settings().admin_ids if await is_enabled(session, a)]
        text = await build_text(session, now)
        await session.commit()
    if recipients:
        await notify_admins(bot, text, only=recipients)
    return True
