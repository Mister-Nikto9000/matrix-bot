"""Автоворонка дожима: три напоминания, каждое уходит человеку один раз.

- teaser_2h — через 2 часа после тизера, если каналы не пройдены;
- full_24h — через 24 часа после полного разбора, если нет приглашённых;
- inactive_3d — через 3 дня без заходов.

Ограничения: не больше 2 пушей в сутки на человека (считаются все пуши), тихие часы 23:00–09:00 по Москве
(в это время ничего не уходит, а накопившееся уходит после 09:00), не пишем заблокировавшим и тем, кто
отключил маркетинг (/stop). Условия считаются по состоянию, поэтому отложенный пуш сам «догоняет» в 09:00.
"""
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot import keyboards as kb
from bot.content import messages as msg
from bot.db.models import Event, PushLog, Referral, User
from bot.services import channels as channels_svc
from bot.services import pushes
from bot.services.clock import to_msk

log = logging.getLogger(__name__)

QUIET_FROM, QUIET_TO = 23, 9  # МСК
MAX_PUSHES_PER_DAY = 2
# Окна давности: после обновления бота не пишем людям, которые давно ушли
TEASER_DELAY, TEASER_MAX_AGE = timedelta(hours=2), timedelta(hours=48)
FULL_DELAY, FULL_MAX_AGE = timedelta(hours=24), timedelta(hours=72)
INACTIVE_DELAY, INACTIVE_MAX_AGE = timedelta(days=3), timedelta(days=14)

KINDS = ("teaser_2h", "full_24h", "inactive_3d")  # порядок = приоритет


def in_quiet_hours(now: datetime) -> bool:
    hour = to_msk(now).hour
    return hour >= QUIET_FROM or hour < QUIET_TO


def _eligible_users():
    return select(User).where(
        User.is_blocked.is_(False), User.notify_marketing.is_(True), User.birth_date.is_not(None)
    )


def _not_pushed(kind: str):
    return User.id.not_in(select(PushLog.user_id).where(PushLog.kind == kind))


async def _first_event_times(session: AsyncSession, name: str, earliest: datetime, latest: datetime) -> set[int]:
    """Пользователи, у которых первое событие name случилось в окне [earliest, latest]."""
    first = func.min(Event.created_at)
    rows = await session.execute(
        select(Event.user_id).where(Event.name == name, Event.user_id.is_not(None))
        .group_by(Event.user_id).having(first >= earliest, first <= latest)
    )
    return {uid for (uid,) in rows}


async def candidates(session: AsyncSession, kind: str, now: datetime) -> list[User]:
    base = _eligible_users().where(_not_pushed(kind))
    if kind == "teaser_2h":
        ids = await _first_event_times(session, "teaser_shown", now - TEASER_MAX_AGE, now - TEASER_DELAY)
        stmt = base.where(User.id.in_(ids), User.channels_ok_at.is_(None))
    elif kind == "full_24h":
        ids = await _first_event_times(session, "full_shown", now - FULL_MAX_AGE, now - FULL_DELAY)
        has_invited = select(Referral.inviter_id)
        stmt = base.where(User.id.in_(ids), User.id.not_in(has_invited))
    elif kind == "inactive_3d":
        stmt = base.where(User.last_seen_at.is_not(None), User.last_seen_at <= now - INACTIVE_DELAY,
                          User.last_seen_at >= now - INACTIVE_MAX_AGE)
    else:
        raise ValueError(kind)
    return list(await session.scalars(stmt.order_by(User.id)))


def build(kind: str, has_channels: bool) -> tuple[str, InlineKeyboardMarkup]:
    if kind == "teaser_2h":
        text = msg.PUSH_TEASER_CHANNELS if has_channels else msg.PUSH_TEASER_NO_CHANNELS
        return text, kb.push_kb(kind, [(msg.BTN_PUSH_OPEN_FULL, "full")])
    if kind == "full_24h":
        return msg.PUSH_FULL_24H, kb.push_kb(kind, [(msg.BTN_PUSH_COMPAT, "compat")])
    return msg.PUSH_INACTIVE_3D, kb.push_kb(kind, [(msg.BTN_PUSH_TODAY, "today"), (msg.BTN_PUSH_SPHERES, "spheres")])


async def pushes_last_day(session: AsyncSession, user_id: int, now: datetime) -> int:
    return await session.scalar(
        select(func.count()).select_from(PushLog).where(PushLog.user_id == user_id, PushLog.sent_at > now - timedelta(hours=24))
    ) or 0


async def run_funnel(bot: Bot, sessionmaker: async_sessionmaker, now: datetime) -> dict[str, int]:
    """Отправляет подошедшие пуши. Возвращает {вид: сколько отправлено}."""
    sent = {kind: 0 for kind in KINDS}
    if in_quiet_hours(now):
        return sent
    async with sessionmaker() as session:
        has_channels = bool(await channels_svc.active_channels(session))
        for kind in KINDS:
            for user in await candidates(session, kind, now):
                try:
                    if await pushes_last_day(session, user.id, now) >= MAX_PUSHES_PER_DAY:
                        continue
                    text, markup = build(kind, has_channels)
                    result = await pushes.send_push(bot, session, user, kind, text, markup, now=now)
                except Exception:  # noqa: BLE001 — сбой одного получателя не прерывает рассылку
                    log.exception("funnel push %s failed for user %s", kind, user.id)
                    await session.rollback()
                    continue
                sent[kind] += result == "sent"
    return sent


