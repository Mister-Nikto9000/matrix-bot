from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kb
from bot.content import messages as msg
from bot.db.models import Unlock, User
from bot.handlers import matrix
from bot.services.dates import format_date
from bot.states import DateInput

router = Router(name="common")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(msg.HELP)


@router.message(Command("settings"))
async def cmd_settings(message: Message, user: User) -> None:
    if user.birth_date:
        text = msg.SETTINGS.format(date=format_date(user.birth_date))
    else:
        text = msg.SETTINGS_NO_DATE
    await message.answer(text, reply_markup=kb.settings_kb())


@router.callback_query(F.data == "change_date")
async def cb_change_date(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(DateInput.waiting)
    await call.message.answer(msg.ASK_DATE)
    await call.answer()


@router.callback_query(F.data == "change_gender")
async def cb_change_gender(call: CallbackQuery, state: FSMContext) -> None:
    await matrix.ask_gender(call.message, state, None)
    await call.answer()


@router.message(Command("deletedata"))
async def cmd_deletedata(message: Message) -> None:
    await message.answer(msg.DELETE_CONFIRM, reply_markup=kb.delete_confirm_kb())


@router.callback_query(F.data == "delete_yes")
async def cb_delete_yes(call: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    user.birth_date = None
    user.gender = None
    await session.execute(delete(Unlock).where(Unlock.user_id == user.id))
    await state.set_state(DateInput.waiting)
    await call.message.edit_text(msg.DELETED)
    await call.answer()


@router.callback_query(F.data == "delete_no")
async def cb_delete_no(call: CallbackQuery) -> None:
    await call.message.edit_text(msg.DELETE_CANCELLED)
    await call.answer()
