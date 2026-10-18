"""Число дня: сообщение, предложение подписки, ежедневная рассылка."""
import logging
from datetime import date, datetime, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot import keyboards as kb
from bot.content import messages as msg
from bot.db.models import Event, PushLog, User
from bot.services import events, pushes, texts, unlocks
from bot.services.clock import to_msk
from bot.services.matrix_calc import calculate, personal_day

log = logging.getLogger(__name__)

KIND = "daily"
LATE_HOURS = 2  # после простоя бота догоняем рассылку не позже чем через столько часов


def log_kind(day: date) -> str:
    """Ключ дедупликации в push_log: один пуш числа дня в сутки."""
    return f"{KIND}:{day.isoformat()}"


def tomorrow_item(day: date) -> str:
    """Разовый доступ к прогнозу на завтра хранится как unlock с датой."""
    return f"tomorrow:{(day + timedelta(days=1)).isoformat()}"


def day_text(user: User, day: date) -> tuple[int, str]:
    n = personal_day(calculate(user.birth_date), day)
    body = texts.text(n, "day", texts.user_key(user.tg_id))
    return n, msg.DAILY.format(num=n, name=texts.name(n), text=body)


async def extra_lines(session: AsyncSession, user: User, day: date) -> str:
    """Дополнения в конце сообщения (квест дня подключается в своём блоке)."""
    return ""


async def build_push(session: AsyncSession, user: User, day: date, kind: str = KIND) -> tuple[str, InlineKeyboardMarkup]:
    _, text = day_text(user, day)
    extra = await extra_lines(session, user, day)
    if extra:
        text += f"\n\n{extra}"
    opened = await unlocks.is_unlocked(session, user.id, tomorrow_item(day))
    return text, kb.daily_kb(opened, kind)


# --- Предложение подписки после полного разбора ---

async def should_offer(session: AsyncSession, user: User) -> bool:
    if user.notify_daily or user.birth_date is None:
        return False
    shown = await session.scalar(select(Event.id).where(Event.user_id == user.id, Event.name == "daily_offer_shown"))
    return shown is None


async def send_offer(message: Message, session: AsyncSession, user: User) -> None:
    if not await should_offer(session, user):
        return
    await message.answer(msg.DAILY_OFFER, reply_markup=kb.daily_offer_kb())
    await events.track(session, user.id, "daily_offer_shown")


# --- Рассылка ---

async def due_users(session: AsyncSession, day: date, hour: int) -> list[User]:
    """Подписанные, у кого наступило (или было недавно) выбранное время и сегодня число дня ещё не отправлено."""
    sent_today = select(PushLog.user_id).where(PushLog.kind == log_kind(day))
    hours = [h for h in range(hour - LATE_HOURS, hour + 1) if h >= 0]
    rows = await session.scalars(
        select(User).where(
            User.notify_daily.is_(True), User.is_blocked.is_(False), User.birth_date.is_not(None),
            User.notify_hour.in_(hours), User.id.not_in(sent_today),
        ).order_by(User.id)
    )
    return list(rows)


async def run_daily(bot: Bot, sessionmaker: async_sessionmaker, now: datetime) -> int:
    """Рассылает число дня всем, кому пора. Возвращает число отправленных."""
    local = to_msk(now)
    day, sent = local.date(), 0
    async with sessionmaker() as session:
        for user in await due_users(session, day, local.hour):
            try:
                text, markup = await build_push(session, user, day)
                result = await pushes.send_push(bot, session, user, KIND, text, markup, log_kind=log_kind(day))
            except Exception:  # noqa: BLE001 — сбой одного получателя не прерывает рассылку
                log.exception("daily push failed for user %s", user.id)
                await session.rollback()
                continue
            sent += result == "sent"
    return sent
