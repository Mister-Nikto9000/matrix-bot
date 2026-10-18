"""Групповые чаты: /matrix выдаёт кнопку-ссылку на бота с реферальным кодом того, кто вызвал.

Даты рождения в группе не собираются: бот ничего не спрашивает и не разбирает сообщения. Все остальные
сообщения в группах глушатся здесь, чтобы личные сценарии (ввод даты и т. п.) в группе не запускались.
"""
import time

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kb
from bot.content import messages as msg
from bot.db.models import User
from bot.handlers.invite import ref_link
from bot.services import events

router = Router(name="group")
router.message.filter(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))

COOLDOWN_SECONDS = 30  # не спамим чат повторными /matrix
_last_reply: dict[int, float] = {}


@router.message(Command("matrix"))
async def cmd_group_matrix(message: Message, session: AsyncSession, user: User | None = None) -> None:
    if user is None:  # анонимный админ группы или канал — реферального кода нет
        return
    now = time.monotonic()
    if now - _last_reply.get(message.chat.id, -COOLDOWN_SECONDS) < COOLDOWN_SECONDS:
        return
    _last_reply[message.chat.id] = now
    await message.answer(msg.GROUP_MATRIX, reply_markup=kb.group_matrix_kb(ref_link(user)))
    await events.track(session, user.id, "group_matrix", {"chat_type": message.chat.type})


@router.message()
async def ignore_group_messages(message: Message) -> None:
    """Всё остальное в группе бота не касается."""
