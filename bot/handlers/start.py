from datetime import datetime, timezone
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kb
from bot.content import messages as msg
from bot.db.models import Referral, User
from bot.handlers import channels, compat, invite, matrix
from bot.services import events, referrals, users
from bot.services.dates import format_date, parse_birth_date
from bot.services.gender import GENDERS
from bot.states import DateInput, GenderInput

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    is_new_user: bool,
    state: FSMContext,
    bot: Bot,
) -> None:
    args = (command.args or "").strip()
    await events.track(session, user.id, "start", {"args": args} if args else None)
    if user.source is None:  # источник фиксируется один раз — при первом /start
        user.source = users.source_from_args(args)
    await state.clear()

    gift = ""
    if args.startswith("r_"):
        inviter = await users.by_ref_code(session, args[2:])
        # Пригласивший фиксируется только при первом /start
        if inviter and is_new_user and await referrals.attach(session, user, inviter, "ref"):
            gift = msg.START_REF_GIFT
    elif args.startswith("c_"):
        inviter = await users.by_ref_code(session, args[2:])
        if inviter and inviter.id == user.id:
            await message.answer(msg.COMPAT_SELF)
            return
        if inviter:
            if is_new_user:
                await referrals.attach(session, user, inviter, "comp")
            if user.birth_date:
                if await matrix.need_gender(message, user, state, f"compat:{inviter.id}"):
                    return
                await compat.run_pair(bot, session, inviter, user, message)
                return
            await state.update_data(compat_with=inviter.id)
            name = escape(users.display_name(inviter))
            if user.gender is None:
                # приглашение → вопрос об обращении → дата
                await message.answer(msg.START_COMPAT_INTRO.format(name=name))
                await matrix.ask_gender(message, state, "date")
                return
            await state.set_state(DateInput.waiting)
            await message.answer(msg.START_COMPAT.format(name=name))
            return

    if user.birth_date:
        if await matrix.need_gender(message, user, state, "teaser"):
            return
        await matrix.send_teaser(message, session, user)
        return
    if user.gender is None:
        # приветствие (и подарок) → вопрос об обращении → дата
        await message.answer(msg.START_HELLO + gift)
        await matrix.ask_gender(message, state, "date")
        return
    await message.answer(msg.START + gift)
    await state.set_state(DateInput.waiting)


async def process_birth_date(
    message: Message, text: str, session: AsyncSession, user: User, state: FSMContext, bot: Bot
) -> None:
    birth_date = parse_birth_date(text)
    if birth_date is None:
        await message.answer(msg.DATE_INVALID)
        return

    user.birth_date = birth_date
    await events.track(session, user.id, "birth_date_set", {"date": birth_date.isoformat()})
    first_activation = user.activated_at is None
    if first_activation:
        user.activated_at = datetime.now(timezone.utc)
    compat_with = (await state.get_data()).get("compat_with")
    await state.clear()
    await message.answer(msg.DATE_SAVED.format(date=format_date(birth_date)))
    await matrix.send_teaser(message, session, user)

    if first_activation:
        activation = await referrals.on_activated(session, user)
        if activation:
            await invite.notify_inviter(bot, session, activation)
        if compat_with is None:
            # FSM живёт в памяти: после перезапуска бота берём пару из реферальной записи
            ref = await session.scalar(select(Referral).where(Referral.invited_id == user.id, Referral.kind == "comp"))
            compat_with = ref.inviter_id if ref else None
    # Восстанавливает награды за друзей, если пользователь делал /deletedata
    await referrals.apply_rewards(session, user)

    if compat_with:
        inviter = await session.get(User, compat_with)
        if inviter:
            await compat.run_pair(bot, session, inviter, user, message)


@router.callback_query(F.data.startswith("gender:"))
async def cb_gender(call: CallbackQuery, session: AsyncSession, user: User, state: FSMContext, bot: Bot) -> None:
    gender = call.data.split(":", 1)[1]
    if gender not in GENDERS:
        await call.answer()
        return
    user.gender = gender
    await events.track(session, user.id, "gender_set", {"gender": gender})
    await call.answer()
    try:
        await call.message.edit_reply_markup(reply_markup=None)  # чтобы не нажимали повторно
    except TelegramBadRequest:
        pass
    after = None
    if await state.get_state() == GenderInput.waiting.state:
        after = (await state.get_data()).get("after")
        await state.set_state(None)
    await resume_after_gender(call.message, after, session, user, state, bot)


async def resume_after_gender(
    message: Message, after: str | None, session: AsyncSession, user: User, state: FSMContext, bot: Bot
) -> None:
    """Показываем то, ради чего спрашивали обращение."""
    kind, _, arg = (after or "").partition(":")
    if not kind:  # из /settings или по старой кнопке
        await message.answer(msg.GENDER_SAVED)
    elif kind == "date":
        await state.set_state(DateInput.waiting)
        if arg:  # дату прислали вместо ответа на вопрос — обрабатываем её
            await process_birth_date(message, arg, session, user, state, bot)
        else:
            await message.answer(msg.ASK_DATE)
    elif user.birth_date is None:
        await matrix.ask_date(message, state)
    elif kind == "teaser":
        await matrix.send_teaser(message, session, user)
    elif kind == "full":
        await channels.open_full(message, bot, session, user, state)
    elif kind == "sphere":
        await matrix.send_sphere(message, session, user, arg)
    elif kind == "compat" and arg.isdigit() and (inviter := await session.get(User, int(arg))):
        await compat.run_pair(bot, session, inviter, user, message)
    else:
        await matrix.send_teaser(message, session, user)


@router.message(StateFilter(GenderInput.waiting), F.text, ~F.text.startswith("/"))
async def on_text_instead_of_gender(message: Message, user: User, state: FSMContext) -> None:
    after = (await state.get_data()).get("after")
    if after and after.startswith("date") and parse_birth_date(message.text) is not None:
        after = f"date:{message.text}"  # прислали дату — запомним и обработаем после ответа
    await matrix.ask_gender(message, state, after)


@router.message(F.text, ~F.text.startswith("/"))
async def on_text(message: Message, session: AsyncSession, user: User, state: FSMContext, bot: Bot) -> None:
    if await state.get_state() == DateInput.waiting.state or user.birth_date is None:
        if user.gender is None:
            await matrix.ask_gender(message, state, f"date:{message.text}")
            return
        await process_birth_date(message, message.text, session, user, state, bot)
    else:
        await message.answer(msg.USE_BUTTONS)
