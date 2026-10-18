"""Алерты админам. Каждый тип — не чаще раза в час."""
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.services import channels as channels_svc
from bot.services import state, stats
from bot.services.clock import as_utc, now_utc
from bot.services.notify import notify_admins

log = logging.getLogger(__name__)

ALERT_INTERVAL = timedelta(hours=1)
ERRORS_PER_HOUR_LIMIT = 20
K_MIN_ACTIVATED = 20  # K считаем осмысленным, только если активированных за сутки больше


async def _throttled(session: AsyncSession, kind: str, now: datetime) -> bool:
    """True — такой алерт уже отправляли меньше часа назад."""
    raw = await state.get(session, f"alert:{kind}")
    if raw:
        last = as_utc(datetime.fromisoformat(raw))
        if now - last < ALERT_INTERVAL:
            return True
    return False


async def _mark_sent(session: AsyncSession, kind: str, now: datetime) -> None:
    await state.set_value(session, f"alert:{kind}", now.isoformat())


async def collect_alerts(bot: Bot, session: AsyncSession, now: datetime) -> list[tuple[str, str]]:
    """[(тип, текст)] — что сейчас стоит сообщить, без учёта ограничения «раз в час»."""
    found: list[tuple[str, str]] = []

    for ch in await channels_svc.active_channels(session):
        is_admin, _ = await channels_svc.bot_rights(bot, ch.chat_id)
        if not is_admin:
            found.append((f"channel_admin:{ch.chat_id}", (
                f"⚠️ Бот больше не администратор в канале «{ch.title}» (#{ch.id}). "
                "Проверка подписки на него не работает — верни права или отключи канал в /channels."
            )))

    errors = await stats.errors_in_last_hour(session, now)
    if errors > ERRORS_PER_HOUR_LIMIT:
        found.append(("errors", f"🔥 За последний час необработанных ошибок: {errors} (порог {ERRORS_PER_HOUR_LIMIT}). Смотри logs/bot.log."))

    k24, activated24 = await stats.k_window(session, now - timedelta(hours=24), now)
    if activated24 > K_MIN_ACTIVATED:
        k7, _ = await stats.k_window(session, now - timedelta(days=7), now)
        if k7 > 0 and k24 < k7 / 2:
            found.append(("k_drop", (
                f"📉 K за 24 часа упал: {k24:.2f} при среднем за 7 дней {k7:.2f} "
                f"(активированных за сутки: {activated24})."
            )))
    return found


async def run_alerts(bot: Bot, sessionmaker: async_sessionmaker, now: datetime | None = None) -> int:
    """Проверяет условия и рассылает новые алерты. Возвращает число отправленных."""
    now = now or now_utc()
    sent = 0
    async with sessionmaker() as session:
        to_send = []
        for kind, text in await collect_alerts(bot, session, now):
            if await _throttled(session, kind, now):
                continue
            await _mark_sent(session, kind, now)
            to_send.append(text)
        await session.commit()
    for text in to_send:
        await notify_admins(bot, text)
        sent += 1
    return sent
