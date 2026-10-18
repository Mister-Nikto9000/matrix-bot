"""Фоновые задачи бота: собираются в один планировщик (см. services/scheduler.py)."""
from datetime import datetime

from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.services import alerts, daily, digest
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

    scheduler.add("daily", daily_job)
    scheduler.add("digest", digest_job)
    scheduler.add("alerts", alerts_job)
    return scheduler
