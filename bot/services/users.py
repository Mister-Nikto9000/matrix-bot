import logging
import secrets
import string

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.services.gender import render

log = logging.getLogger(__name__)

_ALPHABET = string.ascii_lowercase + string.digits


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
