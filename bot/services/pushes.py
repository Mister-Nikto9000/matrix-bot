"""Отправка пушей: общий ограничитель скорости, обработка блокировок и лимитов Telegram, журнал push_log.

Каждый пуш сначала «занимается» записью в push_log (unique по пользователю и виду) и только потом
уходит: при сбое он не повторится, дубли исключены даже при перезапуске бота.
"""
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import PushLog, User
from bot.services import events
from bot.services.gender import render, render_markup

log = logging.getLogger(__name__)

SEND_PER_SECOND = 20  # лимит Telegram на рассылки — около 30/с, оставляем запас
MAX_RETRIES = 3


class RateLimiter:
    """Не больше per_second отправок в секунду на всех: следующая отправка не раньше чем через 1/per_second."""

    def __init__(
        self,
        per_second: float = SEND_PER_SECOND,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ):
        self.interval = 1.0 / per_second
        self._clock = clock
        self._sleep = sleep or asyncio.sleep
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock()
            wait = self._next - now
            if wait > 0:
                await self._sleep(wait)
                now = self._next
            self._next = max(now, self._next) + self.interval


limiter = RateLimiter()
_sleep = asyncio.sleep  # в тестах подменяется, чтобы не ждать retry_after по-настоящему


async def deliver(bot: Bot, user: User, text: str, markup: InlineKeyboardMarkup | None = None) -> str:
    """Отправляет сообщение пользователю. Возвращает sent / blocked / failed."""
    text = render(text, user.gender)
    markup = render_markup(markup, user.gender)
    for _ in range(MAX_RETRIES):
        await limiter.acquire()
        try:
            await bot.send_message(user.tg_id, text, reply_markup=markup, disable_web_page_preview=True)
            return "sent"
        except TelegramRetryAfter as e:  # Telegram просит притормозить
            log.warning("RetryAfter %ss while sending to %s", e.retry_after, user.tg_id)
            await _sleep(e.retry_after + 1)
        except TelegramForbiddenError:
            user.is_blocked = True
            return "blocked"
        except TelegramAPIError as e:
            log.warning("Push to %s failed: %s", user.tg_id, e)
            return "failed"
    return "failed"


async def already_sent(session: AsyncSession, user_id: int, log_kind: str) -> bool:
    return bool(await session.scalar(select(PushLog.id).where(PushLog.user_id == user_id, PushLog.kind == log_kind)))


async def send_push(
    bot: Bot,
    session: AsyncSession,
    user: User,
    kind: str,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
    log_kind: str | None = None,
) -> str:
    """Один пуш человеку. kind — вид для отчётов, log_kind — ключ дедупликации (по умолчанию = kind).

    Возвращает sent / blocked / failed / skipped (такой пуш уже отправляли).
    """
    log_kind = log_kind or kind
    if await already_sent(session, user.id, log_kind):
        return "skipped"
    session.add(PushLog(user_id=user.id, kind=log_kind))
    await session.commit()  # занимаем место до отправки: при сбое не будет дублей
    result = await deliver(bot, user, text, markup)
    if result == "sent":
        await events.track(session, user.id, "push_sent", {"kind": kind})
    elif result == "blocked":
        await events.track(session, user.id, "push_blocked", {"kind": kind})
    await session.commit()
    return result
