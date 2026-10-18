import os

# Настройки для импорта модулей бота в тестах (реальный токен не нужен)
os.environ.setdefault("BOT_TOKEN", "123:test")
os.environ.setdefault("BOT_USERNAME", "test_bot")
os.environ.setdefault("ADMIN_IDS", "999")

import itertools  # noqa: E402
from datetime import datetime  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from aiogram import Bot  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.methods import AnswerCallbackQuery, CreateChatInviteLink, GetChat, GetChatMember, SendMessage  # noqa: E402
from aiogram.types import (  # noqa: E402
    ChatFullInfo,
    ChatInviteLink,
    ChatMemberAdministrator,
    ChatMemberLeft,
    ChatMemberMember,
    Message,
    Update,
)
from aiogram.types import User as TgUser  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from bot.content import messages as msg  # noqa: E402
from bot.db.models import Base  # noqa: E402
from bot.main import build_dispatcher  # noqa: E402
from bot.middlewares import DbMiddleware, RenderMiddleware  # noqa: E402
from bot.services import channels as channels_svc  # noqa: E402
from bot.services import users as users_svc  # noqa: E402


class FakeTelegram(BaseSession):
    """Подменяет Bot API: записывает вызовы и отдаёт правдоподобные ответы."""

    def __init__(self):
        super().__init__()
        self.calls: list = []
        self.members: dict[tuple[int, int], str] = {}  # (chat_id, user_id) -> member/administrator
        self.chats: dict[str, int] = {}  # "@username" -> chat_id
        self.forbidden: set[int] = set()  # chat_id, которые заблокировали бота
        self.retry_once: dict[int, int] = {}  # chat_id -> retry_after: первая отправка получит 429
        self.titles: dict[int, str] = {}  # chat_id -> название канала
        self._ids = itertools.count(1)

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage):
            if "BADHTML" in (method.text or ""):
                from aiogram.exceptions import TelegramBadRequest
                raise TelegramBadRequest(method=method, message="Bad Request: can't parse entities")
            if method.chat_id in self.retry_once:
                from aiogram.exceptions import TelegramRetryAfter
                raise TelegramRetryAfter(method=method, message="Too Many Requests", retry_after=self.retry_once.pop(method.chat_id))
            if method.chat_id in self.forbidden:
                from aiogram.exceptions import TelegramForbiddenError
                raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
            return Message.model_validate(
                {"message_id": next(self._ids), "date": int(datetime.now().timestamp()),
                 "chat": {"id": method.chat_id, "type": "private"}, "text": method.text},
                context={"bot": bot},
            )
        if isinstance(method, GetChatMember):
            user = TgUser(id=method.user_id, is_bot=method.user_id == bot.id, first_name="x")
            status = self.members.get((method.chat_id, method.user_id))
            if status in ("administrator", "administrator+invite"):
                return ChatMemberAdministrator.model_construct(
                    status="administrator", user=user, can_invite_users=status == "administrator+invite"
                )
            if status == "member":
                return ChatMemberMember(user=user)
            return ChatMemberLeft(user=user)
        if isinstance(method, GetChat):
            chat_id = self.chats.get(method.chat_id, method.chat_id)
            if not isinstance(chat_id, int):
                from aiogram.exceptions import TelegramBadRequest
                raise TelegramBadRequest(method=method, message="Bad Request: chat not found")
            return ChatFullInfo.model_construct(id=chat_id, type="channel", title=self.titles.get(chat_id, f"Канал {chat_id}"))
        if isinstance(method, CreateChatInviteLink):
            # ссылку создаёт только админ с правом «Пригласительные ссылки»
            if self.members.get((method.chat_id, bot.id)) != "administrator+invite":
                from aiogram.exceptions import TelegramBadRequest
                raise TelegramBadRequest(method=method, message="Bad Request: not enough rights to manage chat invite links")
            n = next(self._ids)
            return ChatInviteLink(
                invite_link=f"https://t.me/+link{n}", creator=TgUser(id=bot.id, is_bot=True, first_name="bot"),
                creates_join_request=bool(method.creates_join_request), is_primary=False, is_revoked=False,
                name=method.name,
            )
        return True

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


class TG:
    """Эмулятор переписки с ботом."""

    def __init__(self, dp, bot, fake):
        self.dp, self.bot, self.fake = dp, bot, fake
        self._upd = itertools.count(1)
        # Как отвечать на вопрос «Как к тебе обращаться?»: "f"/"m" — сразу нажимаем кнопку
        # (чтобы сценарии про другое не спотыкались о вопрос), None — не отвечаем
        self.auto_gender: str | None = "f"

    async def _answer_gender(self, calls: list, tg_id: int, first_name: str) -> list:
        if self.auto_gender and msg.GENDER_ASK in sent_texts(calls, tg_id):
            calls = calls + await self.click(tg_id, f"gender:{self.auto_gender}", first_name)
        return calls

    async def _feed(self, payload: dict) -> list:
        start = len(self.fake.calls)
        update = Update.model_validate({"update_id": next(self._upd), **payload}, context={"bot": self.bot})
        await self.dp.feed_update(self.bot, update)
        calls = self.fake.calls[start:]
        assert msg.ERROR not in sent_texts(calls), "handler raised an error, see log"
        return calls

    def _user(self, tg_id, first_name):
        return {"id": tg_id, "is_bot": False, "first_name": first_name}

    async def send(self, tg_id: int, text: str, first_name: str = "Аня") -> list:
        entities = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] if text.startswith("/") else None
        calls = await self._feed({"message": {
            "message_id": 1, "date": int(datetime.now().timestamp()),
            "chat": {"id": tg_id, "type": "private"}, "from": self._user(tg_id, first_name),
            "text": text, "entities": entities,
        }})
        return await self._answer_gender(calls, tg_id, first_name)

    async def join_request(self, tg_id: int, chat_id: int) -> list:
        return await self._feed({"chat_join_request": {
            "chat": {"id": chat_id, "type": "channel", "title": "Закрытый"},
            "from": self._user(tg_id, "Аня"), "user_chat_id": tg_id, "date": int(datetime.now().timestamp()),
        }})

    async def forward(self, tg_id: int, chat_id: int, title: str, text: str = "пост") -> list:
        """Пересылка поста из канала."""
        now = int(datetime.now().timestamp())
        return await self._feed({"message": {
            "message_id": 1, "date": now, "chat": {"id": tg_id, "type": "private"},
            "from": self._user(tg_id, "Админ"), "text": text,
            "forward_origin": {"type": "channel", "date": now, "message_id": 7,
                               "chat": {"id": chat_id, "type": "channel", "title": title}},
        }})

    async def bot_status(self, chat_id: int, title: str, old: str, new: str, by: int = 777) -> list:
        """my_chat_member: статус бота в канале сменился с old на new (member/administrator/left/kicked)."""
        def member(status):
            m = {"status": status, "user": {"id": self.bot.id, "is_bot": True, "first_name": "bot"}}
            if status == "administrator":
                # все обязательные права — False (их список меняется с версиями Bot API)
                m.update({name: False for name, f in ChatMemberAdministrator.model_fields.items()
                          if f.is_required() and name not in ("status", "user")})
                m["can_invite_users"] = True
            if status == "kicked":
                m["until_date"] = 0
            return m
        return await self._feed({"my_chat_member": {
            "chat": {"id": chat_id, "type": "channel", "title": title}, "from": self._user(by, "Владелец"),
            "date": int(datetime.now().timestamp()), "old_chat_member": member(old), "new_chat_member": member(new),
        }})

    async def inline(self, tg_id: int, query: str = "", first_name: str = "Аня") -> list:
        """Пользователь набрал @бот в чате."""
        return await self._feed({"inline_query": {
            "id": str(next(self._upd)), "from": self._user(tg_id, first_name), "query": query, "offset": "",
        }})

    async def chosen(self, tg_id: int, result_id: str, first_name: str = "Аня") -> list:
        """Пользователь выбрал карточку (нужен включённый inline feedback у бота)."""
        return await self._feed({"chosen_inline_result": {
            "result_id": result_id, "from": self._user(tg_id, first_name), "query": "",
        }})

    async def click(self, tg_id: int, data: str, first_name: str = "Аня") -> list:
        calls = await self._feed({"callback_query": {
            "id": str(next(self._upd)), "from": self._user(tg_id, first_name), "chat_instance": "ci", "data": data,
            "message": {"message_id": 1, "date": int(datetime.now().timestamp()),
                        "chat": {"id": tg_id, "type": "private"}, "text": "..."},
        }})
        if data.startswith("gender:"):
            return calls
        return await self._answer_gender(calls, tg_id, first_name)


def sent_texts(calls: list, chat_id: int | None = None) -> list[str]:
    return [c.text for c in calls if isinstance(c, SendMessage) and (chat_id is None or c.chat_id == chat_id)]


def sent(calls: list, chat_id: int | None = None) -> list[SendMessage]:
    return [c for c in calls if isinstance(c, SendMessage) and (chat_id is None or c.chat_id == chat_id)]


def alerts(calls: list) -> list[str]:
    return [c.text for c in calls if isinstance(c, AnswerCallbackQuery) and c.show_alert]


def buttons(call: SendMessage) -> list:
    return [b for row in (call.reply_markup.inline_keyboard if call.reply_markup else []) for b in row]


_db_mw = DbMiddleware(None)
_dp = build_dispatcher(_db_mw)


@pytest_asyncio.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 't.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def tg(db):
    _db_mw.sessionmaker = db
    _dp.fsm.storage = MemoryStorage()
    channels_svc._cache.clear()
    users_svc._seen_today.clear()
    fake = FakeTelegram()
    bot = Bot("123:test", session=fake, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    fake.middleware(RenderMiddleware())
    yield TG(_dp, bot, fake)


@pytest.fixture(autouse=True)
def _no_pauses(monkeypatch):
    """Паузы между сообщениями разбора в тестах не нужны."""
    from bot.handlers import matrix
    monkeypatch.setattr(matrix, "FULL_PAUSE", 0)


@pytest.fixture(autouse=True)
def _fast_pushes(monkeypatch):
    """Рассылки в тестах не ждут лимитов скорости и retry_after."""
    from bot.services import pushes

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(pushes, "limiter", pushes.RateLimiter(10_000_000, sleep=no_sleep))
    monkeypatch.setattr(pushes, "_sleep", no_sleep)


@pytest.fixture
def anyio_backend():
    return "asyncio"
