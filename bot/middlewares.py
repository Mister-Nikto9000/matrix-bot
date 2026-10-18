from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.types import CallbackQuery, ChatJoinRequest, ChatMemberUpdated, InlineKeyboardMarkup, Message, TelegramObject
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot import keyboards as kb
from bot.content import messages as msg
from bot.services import channels as channels_svc
from bot.services import health, users
from bot.services.gender import current_user, render


def _is_group_chatter(event: TelegramObject) -> bool:
    chat = getattr(event, "chat", None)
    if not isinstance(event, Message) or chat is None or chat.type not in ("group", "supergroup"):
        return False
    return not (event.text or "").split("@")[0].split()[:1] == ["/matrix"]


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
        health.touch_update()
        async with self.sessionmaker() as session:
            data["session"] = session
            tg_user = data.get("event_from_user")
            token = None
            # Заявка в канал и смена прав бота в канале — не повод заводить пользователя бота;
            # так же как и обычная болтовня в группе: пользователем становится только тот, кто позвал /matrix
            if tg_user and not isinstance(event, (ChatJoinRequest, ChatMemberUpdated)) and not _is_group_chatter(event):
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


# Действия, которые требуют действующей подписки. Тизер, разбор, приглашения и настройки — свободные:
# разбор проверяет подписку сам (handlers/channels.py), приглашения приводят новых людей.
GATED_CALLBACKS = ("sphere:", "story")
GATED_PUSH_ACTIONS = ("spheres", "today", "tomorrow", "story")


def needs_subscription(data: str | None) -> bool:
    if not data:
        return False
    if data.startswith(GATED_CALLBACKS):
        return True
    if data.startswith("pc:"):
        action = data.split(":", 2)[-1]
        return action.startswith("sphere_") or action in GATED_PUSH_ACTIONS
    return False


class SubscriptionGate(BaseMiddleware):
    """Перед сферами, картинкой для сторис и ежедневными кнопками проверяет подписку на активные каналы.

    Отписавшийся получает просьбу вернуться; после «Проверить подписку» бот продолжает с меню.
    Каналов нет или подписка на месте — хендлер отрабатывает как обычно.
    """

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("user")
        if not isinstance(event, CallbackQuery) or user is None or not user.birth_date or not needs_subscription(event.data):
            return await handler(event, data)
        session = data["session"]
        channels = await channels_svc.active_channels(session)
        missing = await channels_svc.missing_channels(data["bot"], session, channels, user.tg_id) if channels else []
        if not missing:
            return await handler(event, data)
        state = data.get("state")
        if state is not None:
            await state.update_data(missing_channels=[ch.id for ch in missing])
        await event.answer()
        text = msg.CHANNELS_LAPSED + (msg.CHANNELS_REQUEST_HINT if any(c.join_mode == "request" for c in missing) else "")
        if event.message is not None:
            await event.message.answer(text, reply_markup=kb.channels_kb(missing))


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
        if isinstance(getattr(method, "caption", None), str):  # подписи к фото и файлам
            method.caption = render(method.caption, gender)
        markup = getattr(method, "reply_markup", None)
        if isinstance(markup, InlineKeyboardMarkup):
            for row in markup.inline_keyboard:
                for button in row:
                    button.text = render(button.text, gender)
        return await make_request(bot, method)
