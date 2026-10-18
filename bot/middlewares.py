from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.types import ChatJoinRequest, ChatMemberUpdated, InlineKeyboardMarkup, TelegramObject
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.services import users
from bot.services.gender import current_user, render


class DbMiddleware(BaseMiddleware):
    """Открывает сессию БД, подгружает (или создаёт) пользователя, коммитит после хендлера."""

    def __init__(self, sessionmaker: async_sessionmaker):
        self.sessionmaker = sessionmaker

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.sessionmaker() as session:
            data["session"] = session
            tg_user = data.get("event_from_user")
            token = None
            # Заявка в канал и смена прав бота в канале — не повод заводить пользователя бота
            if tg_user and not isinstance(event, (ChatJoinRequest, ChatMemberUpdated)):
                user, is_new = await users.get_or_create(session, tg_user.id, tg_user.username, tg_user.first_name)
                await users.touch(session, user)
                data["user"] = user
                data["is_new_user"] = is_new
                token = current_user.set(user)
            try:
                result = await handler(event, data)
            finally:
                if token is not None:
                    current_user.reset(token)
            await session.commit()
            users.mark_seen(session)
            return result


class RenderMiddleware(BaseRequestMiddleware):
    """Подставляет формы {м|ж} во все исходящие тексты и кнопки.

    Род берётся у пользователя текущего апдейта, если сообщение уходит ему. Сообщения
    другим людям (users.safe_send) уже отрендерены по их полу — здесь шаблонов в них нет.
    """

    async def __call__(self, make_request, bot, method):
        user = current_user.get()
        chat_id = getattr(method, "chat_id", None)
        gender = user.gender if user is not None and chat_id in (None, user.tg_id) else None
        if isinstance(getattr(method, "text", None), str):
            method.text = render(method.text, gender)
        markup = getattr(method, "reply_markup", None)
        if isinstance(markup, InlineKeyboardMarkup):
            for row in markup.inline_keyboard:
                for button in row:
                    button.text = render(button.text, gender)
        return await make_request(bot, method)
