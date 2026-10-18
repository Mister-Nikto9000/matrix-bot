import asyncio
from datetime import date
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kb
from bot.content import messages as msg
from bot.db.models import User
from bot.services import events, referrals, texts, unlocks
from bot.services.sending import send_long
from bot.services.matrix_calc import Matrix, calculate, year_forecast
from bot.services.referrals import friends, friends_acc
from bot.states import DateInput, GenderInput

router = Router(name="matrix")

SPHERE_ITEMS = {key: item for key, item, _, _ in msg.SPHERE_BUTTONS}


async def ask_date(message: Message, state: FSMContext) -> None:
    await state.set_state(DateInput.waiting)
    await message.answer(msg.ASK_DATE)


async def ask_gender(message: Message, state: FSMContext, after: str | None) -> None:
    """Спрашиваем обращение. after — что показать после ответа (см. start.resume_after_gender)."""
    await state.set_state(GenderInput.waiting)
    await state.update_data(after=after)
    await message.answer(msg.GENDER_ASK, reply_markup=kb.gender_kb())


async def need_gender(message: Message, user: User, state: FSMContext, after: str) -> bool:
    """Пол ещё не выбран (старые пользователи) — сначала спрашиваем его, разбор покажем потом."""
    if user.gender:
        return False
    await ask_gender(message, state, after)
    return True


# Пауза между двумя сообщениями полного разбора (в тестах обнуляется)
FULL_PAUSE = 1.0

# Если аркан в тизере повторяется, для повтора берём первое предложение следующего поля
TEASER_FALLBACK_FIELDS = ["purpose", "talents", "personality"]


def _block(icon: str, label: str, n: int, body: str) -> str:
    return msg.TEASER_BLOCK.format(icon=icon, label=label, num=n, name=texts.name(n), text=body)


def _title(user: User, named: str, plain: str) -> str:
    first_name = (user.first_name or "").strip()
    return named.format(name=escape(first_name)) if first_name else plain


def teaser_blocks(m: Matrix, key: str | None) -> list[str]:
    blocks, seen = [], {}
    for icon, label, n in (("👤", "Личность", m.a), ("🎁", "Таланты", m.b), ("🧭", "Предназначение", m.purpose)):
        repeat = seen.get(n, 0)
        seen[n] = repeat + 1
        if repeat == 0:
            body = texts.text(n, "teaser", key)
        else:
            field = TEASER_FALLBACK_FIELDS[min(repeat - 1, len(TEASER_FALLBACK_FIELDS) - 1)]
            body = texts.first_sentence(texts.text(n, field, key))
        blocks.append(_block(icon, label, n, body))
    return blocks


# Что показываем в превью закрытого (по приоритету), максимум PREVIEW_LIMIT строк
PREVIEW_ITEMS = ["money", "karma", "relationships"]
PREVIEW_LIMIT = 2


def teaser_previews(m: Matrix, plan: dict[str, int | None]) -> list[str]:
    """Строки «🔒 …» для ещё закрытых сфер; числа — из расчёта, пороги — из шкалы."""
    items = [item for item in PREVIEW_ITEMS if plan.get(item)][:PREVIEW_LIMIT]
    lines = []
    for item in sorted(items, key=lambda i: plan[i]):
        need = friends_acc(plan[item])
        if item == "karma":
            lines.append(msg.TEASER_PREVIEW[item].format(d2=m.d2, d1=m.d1, need=need))
        else:
            num = m.c if item == "money" else m.h
            lines.append(msg.TEASER_PREVIEW[item].format(num=num, name=texts.name(num), need=need))
    return lines


def full_cta(plan: dict[str, int | None]) -> str:
    """Одна строка-призыв: ближайшая награда с учётом уже открытого."""
    reachable = [(item, need) for item, need in plan.items() if need]
    if not reachable:
        return ""
    item, need = reachable[0]
    verb, title = msg.ITEM_CTA[item]
    return msg.FULL_CTA.format(need=friends_acc(need), verb=verb, item=title)


async def _plan(session: AsyncSession, user: User) -> tuple[set[str], dict[str, int | None]]:
    unlocked = await unlocks.get_unlocked(session, user.id)
    return unlocked, referrals.unlock_plan(await referrals.counted_count(session, user.id), unlocked)


async def send_teaser(message: Message, session: AsyncSession, user: User) -> None:
    m = calculate(user.birth_date)
    key = texts.user_key(user.tg_id)
    _, plan = await _plan(session, user)
    previews = teaser_previews(m, plan)
    text = "\n\n".join([
        _title(user, msg.TEASER_TITLE_NAMED, msg.TEASER_TITLE),
        *teaser_blocks(m, key),
        msg.TEASER_FOOTER,
        *(["\n".join(previews)] if previews else []),
    ])
    await message.answer(text, reply_markup=kb.teaser_kb())
    await events.track(session, user.id, "teaser_shown")


async def show_full(message: Message, session: AsyncSession, user: User) -> None:
    """Полный разбор двумя сообщениями (лимит Telegram — 4096 символов), затем меню сфер."""
    m = calculate(user.birth_date)
    key = texts.user_key(user.tg_id)
    first = "\n\n".join([
        _title(user, msg.FULL_TITLE_NAMED, msg.FULL_TITLE),
        _block("👤", "Личность", m.a, texts.text(m.a, "personality", key)),
        _block("🎁", "Таланты", m.b, texts.text(m.b, "talents", key)),
    ])
    second = "\n\n".join([
        _block("🧭", "Предназначение", m.purpose, texts.text(m.purpose, "purpose", key))
        + "\n" + msg.FULL_PURPOSE_POINTS.format(sky=m.sky, earth=m.earth),
        _block("🏡", "Зона комфорта", m.e, texts.text(m.e, "comfort", key)),
    ])
    await send_long(message, first)
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    await asyncio.sleep(FULL_PAUSE)
    await send_long(message, second)
    await send_spheres_menu(message, session, user)
    await events.track(session, user.id, "full_shown")


async def send_spheres_menu(message: Message, session: AsyncSession, user: User) -> None:
    unlocked, plan = await _plan(session, user)
    cta = full_cta(plan)
    text = msg.SPHERES_TITLE + (f"\n\n{cta}" if cta else "")
    await message.answer(text, reply_markup=kb.spheres_kb(unlocked, plan))


def render_sphere(key: str, m: Matrix, today: date | None = None, text_key: str | None = None) -> str:
    if key == "money":
        if m.c == m.money_channel:
            return msg.MONEY_SINGLE.format(c=m.c, c_name=texts.name(m.c), c_text=texts.text(m.c, "money", text_key))
        return msg.MONEY.format(
            c=m.c, c_name=texts.name(m.c), c_text=texts.text(m.c, "money", text_key),
            ch=m.money_channel, ch_name=texts.name(m.money_channel),
            ch_text=texts.text(m.money_channel, "money", text_key),
        )
    if key == "relationships":
        hc = m.relationships_channel
        if m.h == hc:
            return msg.RELATIONSHIPS_SINGLE.format(
                h=m.h, h_name=texts.name(m.h), h_text=texts.text(m.h, "relationships", text_key)
            )
        return msg.RELATIONSHIPS.format(
            h=m.h, h_name=texts.name(m.h), h_text=texts.text(m.h, "relationships", text_key),
            hc=hc, hc_name=texts.name(hc), hc_text=texts.text(hc, "relationships", text_key),
        )
    if key == "karma":
        return msg.KARMA.format(
            tail=m.karmic_tail, d2_name=texts.name(m.d2), d1_name=texts.name(m.d1), d_name=texts.name(m.d),
            text=texts.text(m.d, "karma", text_key),
        )
    if key == "year":
        year = (today or date.today()).year
        n = year_forecast(m, year)
        return msg.YEAR.format(year=year, num=n, name=texts.name(n), text=texts.text(n, "year", text_key))
    return msg.SOON  # practices, ai


@router.message(Command("matrix"))
async def cmd_matrix(message: Message, session: AsyncSession, user: User, state: FSMContext) -> None:
    if not user.birth_date:
        await ask_date(message, state)
        return
    if await need_gender(message, user, state, "teaser"):
        return
    await send_teaser(message, session, user)


@router.callback_query(F.data.startswith("sphere:"))
async def cb_sphere(call: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    key = call.data.split(":", 1)[1]
    item = SPHERE_ITEMS.get(key)
    if item is None:
        await call.answer()
        return
    if not user.birth_date:
        await call.answer()
        await ask_date(call.message, state)
        return
    unlocked = await unlocks.get_unlocked(session, user.id)
    if item not in unlocked:
        plan = referrals.unlock_plan(await referrals.counted_count(session, user.id), unlocked)
        need = plan.get(item)
        alert = msg.SPHERE_LOCKED_ALERT.format(need=friends(need)) if need else msg.SPHERE_LOCKED_SOON
        await call.answer(alert, show_alert=True)
        return
    await call.answer()
    if await need_gender(call.message, user, state, f"sphere:{key}"):
        return
    await send_sphere(call.message, session, user, key)


async def send_sphere(message: Message, session: AsyncSession, user: User, key: str) -> None:
    await message.answer(render_sphere(key, calculate(user.birth_date), text_key=texts.user_key(user.tg_id)))
    await events.track(session, user.id, "sphere_opened", {"sphere": key})
