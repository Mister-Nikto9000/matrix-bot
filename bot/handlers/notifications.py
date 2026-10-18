"""Число дня, настройки рассылок, /stop и обработка нажатий на кнопки пушей."""
from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kb
from bot.content import messages as msg
from bot.db.models import PushLog, User
from bot.handlers import channels, common, compat, invite, matrix
from bot.services import daily, events, texts, unlocks
from bot.services.clock import msk_today, now_utc
from bot.services.matrix_calc import calculate, personal_day

router = Router(name="notifications")


async def _safe_edit(call: CallbackQuery, text: str, markup=None) -> None:
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:  # сообщение не изменилось или уже удалено
        await call.message.answer(text, reply_markup=markup)


async def _refresh_settings(call: CallbackQuery, user: User) -> None:
    try:
        await call.message.edit_text(common.settings_text(user), reply_markup=kb.settings_kb(user))
    except TelegramBadRequest:
        pass


# --- Число дня: подписка ---

@router.callback_query(F.data == "daily:on")
async def cb_daily_on(call: CallbackQuery, user: User) -> None:
    await call.answer()
    if not user.birth_date:
        await call.message.answer(msg.NEED_DATE_FIRST)
        return
    await _safe_edit(call, msg.DAILY_PICK_HOUR, kb.daily_hours_kb())


@router.callback_query(F.data == "daily:time")
async def cb_daily_time(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.answer(msg.DAILY_PICK_HOUR, reply_markup=kb.daily_hours_kb())


@router.callback_query(F.data.startswith("daily:h:"))
async def cb_daily_hour(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    hour = call.data.rsplit(":", 1)[1]
    if not hour.isdigit() or int(hour) not in msg.DAILY_HOURS:
        await call.answer()
        return
    if not user.birth_date:
        await call.answer()
        await call.message.answer(msg.NEED_DATE_FIRST)
        return
    user.notify_daily, user.notify_hour = True, int(hour)
    await events.track(session, user.id, "daily_enabled", {"hour": int(hour)})
    await call.answer()
    await _safe_edit(call, msg.DAILY_ENABLED.format(hour=hour))


@router.callback_query(F.data == "daily:no")
async def cb_daily_no(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    await events.track(session, user.id, "daily_declined")
    await call.answer()
    await _safe_edit(call, msg.DAILY_DECLINED)


@router.callback_query(F.data == "daily:off")
async def cb_daily_off(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    user.notify_daily = False
    await events.track(session, user.id, "daily_disabled")
    await call.answer(msg.DAILY_DISABLED)
    await _refresh_settings(call, user)


# --- Маркетинговые сообщения ---

@router.message(Command("stop"))
async def cmd_stop(message: Message, session: AsyncSession, user: User) -> None:
    user.notify_marketing = False
    await events.track(session, user.id, "marketing_off")
    await message.answer(msg.STOP_DONE)


@router.callback_query(F.data.in_({"mkt:on", "mkt:off"}))
async def cb_marketing(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    user.notify_marketing = call.data == "mkt:on"
    await events.track(session, user.id, "marketing_on" if user.notify_marketing else "marketing_off")
    await call.answer(msg.MARKETING_ON_DONE if user.notify_marketing else msg.MARKETING_OFF_DONE)
    await _refresh_settings(call, user)


# --- Нажатия на кнопки пушей ---

async def mark_clicked(session: AsyncSession, user: User, kind: str) -> None:
    """Первое нажатие на кнопку пуша: clicked_at в push_log и событие push_clicked."""
    row = await session.scalar(
        select(PushLog).where(PushLog.user_id == user.id, PushLog.kind.like(f"{kind}%"))
        .order_by(PushLog.sent_at.desc()).limit(1)
    )
    if row is not None and row.clicked_at is None:
        row.clicked_at = now_utc()
        await events.track(session, user.id, "push_clicked", {"kind": kind})


async def show_tomorrow(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    today = msk_today()
    if not await unlocks.is_unlocked(session, user.id, daily.tomorrow_item(today)):
        await call.answer(msg.TOMORROW_LOCKED_ALERT, show_alert=True)
        return
    await call.answer()
    tomorrow = today + timedelta(days=1)
    n = personal_day(calculate(user.birth_date), tomorrow)
    body = texts.text(n, "day", texts.user_key(user.tg_id))
    await call.message.answer(msg.TOMORROW.format(date=f"{tomorrow:%d.%m}", num=n, name=texts.name(n), text=body))
    await events.track(session, user.id, "tomorrow_opened")


@router.callback_query(F.data.startswith("pc:"))
async def cb_push_click(call: CallbackQuery, bot: Bot, session: AsyncSession, user: User, state: FSMContext) -> None:
    _, kind, action = call.data.split(":", 2)
    await mark_clicked(session, user, kind)
    if not user.birth_date:
        await call.answer()
        await matrix.ask_date(call.message, state)
        return
    if action == "tomorrow":
        await show_tomorrow(call, session, user)
        return
    await call.answer()
    if await matrix.need_gender(call.message, user, state, {"full": "full"}.get(action, "teaser")):
        return
    if action == "invite":
        await invite.send_invite(call.message, session, user)
    elif action == "compat":
        await compat.send_compat_link(call.message, session, user, state)
    elif action == "full":
        await channels.open_full(call.message, bot, session, user, state)
    elif action == "spheres":
        await matrix.send_spheres_menu(call.message, session, user)
    elif action == "today":
        text, markup = await daily.build_push(session, user, msk_today())
        await call.message.answer(text, reply_markup=markup)
    else:
        await matrix.send_teaser(call.message, session, user)
