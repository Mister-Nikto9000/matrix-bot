import logging
import re
import secrets
import string

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import DailyActivity, User
from bot.services.clock import msk_today, now_utc
from bot.services.gender import render

log = logging.getLogger(__name__)

_ALPHABET = string.ascii_lowercase + string.digits

SOURCE_TAG = re.compile(r"^[A-Za-z0-9_]{1,20}$")
_seen_today: set[tuple[int, object]] = set()  # (user_id, день): запись daily_activity уже сделана


def source_from_args(args: str) -> str:
    """Источник трафика по параметру /start: r_ и c_ — ref, s_<тег> — тег, иначе organic."""
    if args.startswith(("r_", "c_")):
        return "ref"
    if args.startswith("s_") and SOURCE_TAG.match(args[2:]):
        return args[2:]
    return "organic"


async def touch(session: AsyncSession, user: User) -> None:
    """Отмечает активность: last_seen_at и одна запись daily_activity в сутки (по Москве)."""
    now = now_utc()
    user.last_seen_at = now
    key = (user.id, msk_today(now))
    if key in _seen_today:
        return
    session.info["seen_key"] = key  # в кэш попадёт только после успешного коммита (см. mark_seen)
    exists = await session.scalar(
        select(DailyActivity.id).where(DailyActivity.user_id == user.id, DailyActivity.day == key[1])
    )
    if not exists:
        try:
            async with session.begin_nested():
                session.add(DailyActivity(user_id=user.id, day=key[1]))
        except IntegrityError:  # параллельный апдейт того же человека успел раньше
            pass


def mark_seen(session: AsyncSession) -> None:
    """Вызывается после коммита: запись daily_activity за сегодня точно сохранена."""
    key = session.info.pop("seen_key", None)
    if key is None:
        return
    _seen_today.add(key)
    if len(_seen_today) > 50_000:  # не растёт бесконечно: оставляем только сегодняшние
        _seen_today.intersection_update({k for k in _seen_today if k[1] == key[1]})


def new_ref_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


async def get_or_create(session: AsyncSession, tg_id: int, username: str | None, first_name: str | None) -> tuple[User, bool]:
    user = await session.scalar(select(User).where(User.tg_id == tg_id))
    if user:
        user.username = username
        user.first_name = first_name
        user.is_blocked = False
        return user, False
    while True:
        code = new_ref_code()
        if not await session.scalar(select(User.id).where(User.ref_code == code)):
            break
    user = User(tg_id=tg_id, username=username, first_name=first_name, ref_code=code, is_blocked=False)
    session.add(user)
    await session.flush()
    return user, True


async def by_ref_code(session: AsyncSession, code: str) -> User | None:
    if not code:
        return None
    return await session.scalar(select(User).where(User.ref_code == code))


async def safe_send(bot: Bot, user: User, text: str, **kwargs) -> bool:
    """Отправка другому пользователю. Если бот заблокирован — помечаем is_blocked.

    Формы {м|ж} подставляются по полу получателя, а не текущего пользователя.
    """
    try:
        await bot.send_message(user.tg_id, render(text, user.gender), **kwargs)
        return True
    except TelegramForbiddenError:
        user.is_blocked = True
        log.info("user %s blocked the bot", user.tg_id)
        return False


def display_name(user: User) -> str:
    return user.first_name or (f"@{user.username}" if user.username else "Твой друг")
