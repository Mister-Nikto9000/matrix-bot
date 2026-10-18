"""Ежедневный квест: «Пригласи 1 друга до 23:59 — откроем прогноз на завтра».

Прогресс — число засчитанных активаций друзей за московские сутки. Награда — разовый доступ к тексту числа
дня на завтра (unlock «tomorrow:<дата>»).
"""
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.content import messages as msg
from bot.db.models import Quest, Referral, User
from bot.services import events, unlocks
from bot.services.clock import day_range_utc, msk_today, now_utc

GOAL = 1


def tomorrow_item(day: date) -> str:
    """Разовый доступ к прогнозу на завтра хранится как unlock с датой завтрашнего дня."""
    return f"tomorrow:{(day + timedelta(days=1)).isoformat()}"


async def friends_activated(session: AsyncSession, user: User, day: date) -> int:
    start, end = day_range_utc(day)
    return await session.scalar(
        select(func.count()).select_from(Referral).where(
            Referral.inviter_id == user.id, Referral.counted.is_(True),
            Referral.activated_at >= start, Referral.activated_at < end,
        )
    ) or 0


async def _complete(session: AsyncSession, user: User, quest: Quest, now: datetime) -> bool:
    """Закрывает квест и выдаёт награду. True — квест выполнен только что."""
    if quest.completed_at is not None or quest.progress < quest.goal:
        return False
    quest.completed_at = now
    quest.rewarded = True
    await unlocks.grant(session, user.id, tomorrow_item(quest.day), "quest")
    await events.track(session, user.id, "quest_done", {"day": quest.day.isoformat()})
    return True


async def get(session: AsyncSession, user: User, day: date) -> Quest | None:
    return await session.scalar(select(Quest).where(Quest.user_id == user.id, Quest.day == day))


async def ensure(session: AsyncSession, user: User, day: date | None = None, now: datetime | None = None) -> Quest | None:
    """Квест на день: создаётся при первом показе. Без даты рождения прогноз на завтра не посчитать — квеста нет."""
    if user.birth_date is None:
        return None
    day = day or msk_today()
    quest = await get(session, user, day)
    if quest is None:
        quest = Quest(user_id=user.id, day=day, goal=GOAL, progress=0, rewarded=False)
        session.add(quest)
        await session.flush()
        await events.track(session, user.id, "quest_shown", {"day": day.isoformat()})
    quest.progress = max(quest.progress, await friends_activated(session, user, day))
    await _complete(session, user, quest, now or now_utc())
    return quest


async def on_friend_activated(session: AsyncSession, inviter: User, now: datetime | None = None) -> bool:
    """Друг активировался: двигаем сегодняшний квест, если он уже показан. True — квест выполнен только что."""
    now = now or now_utc()
    quest = await get(session, inviter, msk_today(now))
    if quest is None:
        return False
    quest.progress = max(quest.progress, await friends_activated(session, inviter, quest.day))
    return await _complete(session, inviter, quest, now)


def text(quest: Quest) -> str:
    """Строка для /invite и числа дня."""
    if quest.completed_at is not None:
        return msg.QUEST_DONE
    return msg.QUEST_LINE.format(progress=min(quest.progress, quest.goal), goal=quest.goal)
