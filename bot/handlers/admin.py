from html import escape

from aiogram import Bot, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import Message, TelegramObject
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.content import messages as msg
from bot.db.models import Channel


class IsAdmin(BaseFilter):
    async def __call__(self, event: TelegramObject, event_from_user=None) -> bool:
        return event_from_user is not None and event_from_user.id in get_settings().admin_ids


router = Router(name="admin")
router.message.filter(IsAdmin())


@router.message(Command("addchannel"))
async def cmd_addchannel(message: Message, command: CommandObject, bot: Bot, session: AsyncSession) -> None:
    args = (command.args or "").split()
    if (
        len(args) not in (2, 3)
        or not args[1].startswith(("https://", "http://"))
        or (len(args) == 3 and args[2].lower() != "request")
    ):
        await message.answer(escape(msg.ADMIN_ADDCHANNEL_USAGE))
        return
    ref, url = args[:2]
    join_mode = "request" if len(args) == 3 else "public"
    chat_ref: int | str = int(ref) if ref.lstrip("-").isdigit() else ref
    try:
        chat = await bot.get_chat(chat_ref)
    except TelegramAPIError:
        await message.answer(msg.ADMIN_CHANNEL_NOT_FOUND.format(ref=escape(ref)))
        return
    title = chat.title or str(chat.id)
    try:
        me = await bot.get_chat_member(chat.id, bot.id)
        is_admin = me.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
    except TelegramAPIError:
        me, is_admin = None, False
    if not is_admin:
        await message.answer(msg.ADMIN_BOT_NOT_ADMIN.format(title=escape(title)))
        return
    # Заявки приходят боту и одобряются только с правом приглашать пользователей
    can_invite = me.status == ChatMemberStatus.CREATOR or bool(getattr(me, "can_invite_users", False))
    if join_mode == "request" and not can_invite:
        await message.answer(msg.ADMIN_BOT_NO_INVITE.format(title=escape(title)))
        return

    existing = await session.scalar(select(Channel).where(Channel.chat_id == chat.id))
    if existing:
        await message.answer(msg.ADMIN_CHANNEL_EXISTS.format(title=escape(existing.title), id=existing.id))
        return
    sort_order = (await session.scalar(select(func.max(Channel.sort_order))) or 0) + 1
    channel = Channel(
        chat_id=chat.id, title=title, url=url, is_active=True, sort_order=sort_order, joined_count=0,
        join_mode=join_mode, auto_approve=False,
    )
    session.add(channel)
    await session.flush()
    await message.answer(msg.ADMIN_CHANNEL_ADDED.format(title=escape(title), id=channel.id))


@router.message(Command("delchannel"))
async def cmd_delchannel(message: Message, command: CommandObject, session: AsyncSession) -> None:
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.answer(escape(msg.ADMIN_DELCHANNEL_USAGE))
        return
    channel = await session.get(Channel, int(arg))
    if channel is None:
        await message.answer(msg.ADMIN_CHANNEL_MISSING.format(id=arg))
        return
    await session.delete(channel)
    await message.answer(msg.ADMIN_CHANNEL_DELETED.format(id=arg))


@router.message(Command("approve"))
async def cmd_approve(message: Message, command: CommandObject, session: AsyncSession) -> None:
    args = (command.args or "").split()
    if len(args) != 2 or not args[0].isdigit() or args[1].lower() not in ("on", "off"):
        await message.answer(escape(msg.ADMIN_APPROVE_USAGE))
        return
    channel = await session.get(Channel, int(args[0]))
    if channel is None:
        await message.answer(msg.ADMIN_CHANNEL_MISSING.format(id=args[0]))
        return
    if channel.join_mode != "request":
        await message.answer(msg.ADMIN_APPROVE_NOT_REQUEST.format(id=channel.id))
        return
    channel.auto_approve = args[1].lower() == "on"
    await message.answer(msg.ADMIN_APPROVE_SET.format(
        title=escape(channel.title), id=channel.id, state="включено" if channel.auto_approve else "выключено"
    ))
