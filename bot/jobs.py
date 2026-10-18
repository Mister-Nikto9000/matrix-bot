"""Фоновые задачи бота: собираются в один планировщик (см. services/scheduler.py)."""
import asyncio
from datetime import datetime

from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import get_settings
from bot.services import alerts, backup, daily, digest, funnel, state
from bot.services.clock import msk_today
from bot.services.scheduler import Scheduler, msk_hm

ALERT_EVERY_MINUTES = 5


def build_scheduler(bot: Bot, sessionmaker: async_sessionmaker) -> Scheduler:
    scheduler = Scheduler()

    async def digest_job(now: datetime) -> None:
        hour, _ = msk_hm(now)
        # Окно 09:00–12:00: если бот был выключен в 09:00, дайджест придёт после запуска; повторов нет
        if digest.DIGEST_HOUR <= hour < digest.DIGEST_HOUR + 3:
            await digest.send_digest(bot, sessionmaker, now)

    async def alerts_job(now: datetime) -> None:
        if now.minute % ALERT_EVERY_MINUTES == 0:
            await alerts.run_alerts(bot, sessionmaker, now)

    async def daily_job(now: datetime) -> None:
        await daily.run_daily(bot, sessionmaker, now)

    async def funnel_job(now: datetime) -> None:
        await funnel.run_funnel(bot, sessionmaker, now)

    async def backup_job(now: datetime) -> None:
        """Ежедневная копия SQLite (если сегодняшней ещё нет, например сразу после запуска)."""
        src = backup.sqlite_path(get_settings().database_url)
        if src is None:
            return
        day = msk_today(now)
        created = await asyncio.to_thread(backup.daily_backup, src, day)
        if created:
            async with sessionmaker() as session:
                await state.set_value(session, "last_backup", day.isoformat())
                await session.commit()

    scheduler.add("backup", backup_job)
    scheduler.add("daily", daily_job)
    scheduler.add("funnel", funnel_job)
    scheduler.add("digest", digest_job)
    scheduler.add("alerts", alerts_job)
    return scheduler
