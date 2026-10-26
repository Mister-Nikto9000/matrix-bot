"""/broadcast: текст → предпросмотр → сегмент → тест себе → запуск с подтверждением."""
import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.content import messages as msg
from bot.db.models import Broadcast, Channel, User
from bot.handlers.admin import IsAdmin
from bot.services import broadcast as bc_svc
from bot.services import channels as channels_svc
from bot.services.gender import render
from bot.states import BroadcastInput

log = logging.getLogger(__name__)

router = Router(name="admin_broadcast")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def _btn(text: str, data: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=text, callback_data=data)]


async def segments_kb(session: AsyncSession) -> InlineKeyboardMarkup:
    rows = [
        _btn(msg.BC_SEGMENT_BTN.format(title=title, count=await bc_svc.segment_size(session, key)), f"bc:seg:{key}")
        for key, title in bc_svc.SEGMENTS.items()
    ]
    for channel in await channels_svc.active_channels(session):
        rows.append(_btn(msg.BC_CHANNEL_BTN.format(title=channel.title), f"bc:seg:{bc_svc.CHANNEL_PREFIX}{channel.id}"))
    rows.append(_btn(msg.BC_BTN_CANCEL, "bc:cancel"))
    return _kb(*rows)


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    await state.set_state(BroadcastInput.text)
    await message.answer(msg.BC_ASK_TEXT)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer(msg.BC_NOTHING_TO_CANCEL)
        return
    await state.clear()
    await message.answer(msg.BC_CANCELLED)


@router.message(Command("broadcasts"))
async def cmd_history(message: Message, session: AsyncSession) -> None:
    rows = list(await session.scalars(select(Broadcast).order_by(Broadcast.id.desc()).limit(10)))
    if not rows:
        await message.answer(msg.BC_HISTORY_EMPTY)
        return
    lines = ["📤 <b>Последние рассылки</b>"]
    lines += [
        f"#{b.id} {b.created_at:%d.%m %H:%M} · {await bc_svc.segment_title(session, b.segment)} · "
        f"отправлено {b.sent}, заблокировали {b.blocked}, ошибки {b.failed} ({b.status})"
        for b in rows
    ]
    await message.answer("\n".join(lines))


@router.message(StateFilter(BroadcastInput.text), F.text, ~F.text.startswith("/"))
async def on_broadcast_text(message: Message, session: AsyncSession, state: FSMContext) -> None:
    # Форматирование Telegram (entities) сохраняем; если его нет — текст считаем HTML-разметкой, набранной руками
    text = message.html_text if message.entities else message.text
    if len(text) > bc_svc.MAX_TEXT:
        await message.answer(msg.BC_TOO_LONG)
        return
    try:
        await message.answer(text)  # предпросмотр: заодно проверяет, что Telegram принимает разметку
    except TelegramBadRequest as e:
        await message.answer(msg.BC_BAD_HTML.format(error=str(e).replace("<", "&lt;")), parse_mode=None)
        return
    await state.update_data(text=text)
    await message.answer(msg.BC_PREVIEW_HEADER, reply_markup=await segments_kb(session))


@router.callback_query(F.data == "bc:cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await call.message.answer(msg.BC_CANCELLED)


async def _draft(call: CallbackQuery, state: FSMContext) -> dict | None:
    data = await state.get_data()
    if "text" not in data:
        await call.answer(msg.BC_EXPIRED, show_alert=True)
        return None
    return data


@router.callback_query(F.data.startswith("bc:seg:"))
async def cb_segment(call: CallbackQuery, bot: Bot, session: AsyncSession, state: FSMContext) -> None:
    if await _draft(call, state) is None:
        return
    key = call.data.split(":", 2)[2]
    channel_id = bc_svc.channel_segment_id(key)
    if key not in bc_svc.SEGMENTS and channel_id is None:
        await call.answer()
        return
    ids = None
    if channel_id is not None:
        channel = await session.get(Channel, channel_id)
        if channel is None:
            await call.answer(msg.BC_CHANNEL_GONE, show_alert=True)
            return
        await call.answer()
        await call.message.answer(msg.BC_CHECKING_CHANNEL.format(title=channel.title))
        try:
            ids = await bc_svc.channel_member_ids(bot, session, channel)
        except TelegramAPIError as e:
            log.warning("channel segment check failed for channel %s: %s", channel_id, e)
            await call.message.answer(msg.BC_CHANNEL_CHECK_FAILED.format(error=str(e).replace("<", "&lt;")))
            return
        count = len(ids)
    else:
        count = await bc_svc.segment_size(session, key)
        await call.answer()
    await state.update_data(segment=key, ids=ids)
    rows = [_btn(msg.BC_BTN_TEST, "bc:test")]
    if count:
        rows.append(_btn(msg.BC_BTN_GO, "bc:go"))
    rows += [_btn(msg.BC_BTN_BACK, "bc:back"), _btn(msg.BC_BTN_CANCEL, "bc:cancel")]
    text = msg.BC_SEGMENT_SUMMARY.format(title=await bc_svc.segment_title(session, key), count=count)
    if not count:
        text += f"\n{msg.BC_EMPTY_SEGMENT}"
    await call.message.answer(text, reply_markup=_kb(*rows))


@router.callback_query(F.data == "bc:back")
async def cb_back(call: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if await _draft(call, state) is None:
        return
    await call.answer()
    await call.message.answer(msg.BC_PREVIEW_HEADER, reply_markup=await segments_kb(session))


@router.callback_query(F.data == "bc:test")
async def cb_test(call: CallbackQuery, state: FSMContext, user: User) -> None:
    data = await _draft(call, state)
    if data is None:
        return
    await call.answer(msg.BC_TEST_SENT)
    await call.message.answer(render(data["text"], user.gender))


@router.callback_query(F.data == "bc:go")
async def cb_go(call: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    data = await _draft(call, state)
    if data is None or "segment" not in data:
        return
    ids = data.get("ids")
    count = len(ids) if ids is not None else await bc_svc.segment_size(session, data["segment"])
    await call.answer()
    await call.message.answer(
        msg.BC_CONFIRM.format(count=count, title=await bc_svc.segment_title(session, data["segment"])),
        reply_markup=_kb(_btn(msg.BC_BTN_CONFIRM.format(count=count), "bc:yes"), _btn(msg.BC_BTN_NO, "bc:back")),
    )


@router.callback_query(F.data == "bc:yes")
async def cb_yes(call: CallbackQuery, bot: Bot, session: AsyncSession, state: FSMContext, user: User) -> None:
    data = await _draft(call, state)
    if data is None or "segment" not in data:
        return
    if any(not t.done() for t in bc_svc.tasks):
        await call.answer(msg.BC_ONE_AT_A_TIME, show_alert=True)
        return
    bc = Broadcast(created_by=call.from_user.id, text=data["text"], segment=data["segment"], status="running")
    session.add(bc)
    await session.commit()  # рассылка читает запись в своей сессии
    await state.clear()
    await call.answer()
    progress_msg = await call.message.answer(
        bc_svc.progress(bc), reply_markup=_kb(_btn(msg.BC_BTN_STOP, f"bc:stop:{bc.id}"))
    )
    task = asyncio.create_task(_run_and_report(bot, session.bind, bc.id, progress_msg, data.get("ids")))
    bc_svc.tasks.add(task)
    task.add_done_callback(bc_svc.tasks.discard)


async def _run_and_report(
    bot: Bot, bind, broadcast_id: int, progress_msg: Message, ids: list[int] | None = None
) -> None:
    sessionmaker = async_sessionmaker(bind, expire_on_commit=False)

    async def on_progress(bc: Broadcast) -> None:
        try:
            await progress_msg.edit_text(
                bc_svc.progress(bc), reply_markup=_kb(_btn(msg.BC_BTN_STOP, f"bc:stop:{bc.id}"))
            )
        except TelegramAPIError:  # «message is not modified» и прочее — прогресс не критичен
            pass

    bc = await bc_svc.run_broadcast(bot, sessionmaker, broadcast_id, on_progress, ids)
    try:
        await progress_msg.edit_text(bc_svc.report(bc))
    except TelegramAPIError:
        pass
    await progress_msg.answer(bc_svc.report(bc))  # отдельным сообщением — чтобы пришло уведомление


@router.callback_query(F.data.startswith("bc:stop:"))
async def cb_stop(call: CallbackQuery) -> None:
    bc_svc.stop_requests.add(int(call.data.rsplit(":", 1)[1]))
    await call.answer(msg.BC_STOPPING)
