"""Админ-инструменты: /link, /user, /export, /digest."""
import csv
import io
from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.content import messages as msg
from bot.db.models import Referral, Unlock, User
from bot.handlers.admin import IsAdmin
from bot.handlers.admin_stats import user_label
from bot.services import digest, health, texts
from bot.services.clock import as_utc, to_msk
from bot.services.matrix_calc import calculate
from bot.services.users import SOURCE_TAG

router = Router(name="admin_tools")
router.message.filter(IsAdmin())

SPHERE_NAMES = {item: title for _, item, _, title in msg.SPHERE_BUTTONS}


@router.message(Command("link"))
async def cmd_link(message: Message, command: CommandObject) -> None:
    tag = (command.args or "").strip()
    if not SOURCE_TAG.match(tag):
        await message.answer(
            "Использование: /link &lt;тег&gt;\nТег — латиница, цифры и «_», до 20 символов, например <code>/link vk_may</code>."
        )
        return
    link = f"https://t.me/{get_settings().bot_username}?start=s_{tag}"
    await message.answer(f"Ссылка для источника <b>{escape(tag)}</b>:\n{link}", disable_web_page_preview=True)


def fmt_dt(dt) -> str:
    return "—" if dt is None else f"{to_msk(dt):%d.%m.%Y %H:%M} МСК"


async def find_user(session: AsyncSession, query: str) -> User | None:
    query = query.strip()
    if query.lstrip("@").isdigit() and not query.startswith("@"):
        return await session.scalar(select(User).where(User.tg_id == int(query)))
    return await session.scalar(select(User).where(func.lower(User.username) == query.lstrip("@").lower()))


async def render_user_card(session: AsyncSession, user: User) -> str:
    gender = {"f": "девушка", "m": "парень"}.get(user.gender or "", "не указан")
    if user.birth_date:
        m = calculate(user.birth_date)
        year, arcana = str(user.birth_date.year), f"{m.a}. {texts.name(m.a)}"
    else:
        year, arcana = "—", "—"
    inviter = await session.get(User, user.referrer_id) if user.referrer_id else None
    started = await session.scalar(select(func.count()).select_from(Referral).where(Referral.inviter_id == user.id)) or 0
    counted = await session.scalar(
        select(func.count()).select_from(Referral).where(Referral.inviter_id == user.id, Referral.counted.is_(True))
    ) or 0
    unlocked = list(await session.scalars(select(Unlock.item).where(Unlock.user_id == user.id)))
    spheres = ", ".join(SPHERE_NAMES[i] for i in unlocked if i in SPHERE_NAMES) or "нет"
    channels = f"пройдены {fmt_dt(user.channels_ok_at)}" if user.channels_ok_at else "не пройдены"
    daily = f"число дня в {user.notify_hour}:00" if user.notify_daily else "число дня выкл."
    marketing = "маркетинг вкл." if user.notify_marketing else "маркетинг выкл."
    return "\n".join([
        f"👤 <b>{user_label(user)}</b> · <code>{user.tg_id}</code>",
        f"Пол: {gender} · год рождения: {year}",
        f"Аркан личности: {arcana}",
        f"Источник: {escape(user.source or 'organic')}",
        f"Пригласил: {user_label(inviter) if inviter else '—'}",
        f"Привёл: {counted} засчитано из {started} стартов",
        f"Открытые сферы: {spheres}",
        f"Каналы: {channels}",
        f"Регистрация: {fmt_dt(user.created_at)} · активация: {fmt_dt(user.activated_at)}",
        f"Последний визит: {fmt_dt(user.last_seen_at)}",
        f"Рассылки: {daily}, {marketing}" + (" · 🚫 заблокировал бота" if user.is_blocked else ""),
    ])


@router.message(Command("user"))
async def cmd_user(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not (command.args or "").strip():
        await message.answer("Использование: /user &lt;tg id или @ник&gt;")
        return
    user = await find_user(session, command.args)
    if user is None:
        await message.answer("Пользователь не найден.")
        return
    await message.answer(await render_user_card(session, user))


EXPORT_FIELDS = [
    "tg_id", "source", "gender", "created_at", "activated_at", "channels_ok_at", "referrer_id", "counted_refs",
    "last_seen_at", "is_blocked",
]


async def build_export(session: AsyncSession) -> bytes:
    """CSV без даты рождения, имён и username."""
    counted = dict((await session.execute(
        select(Referral.inviter_id, func.count()).where(Referral.counted.is_(True)).group_by(Referral.inviter_id)
    )).all())
    tg_by_id = dict((await session.execute(select(User.id, User.tg_id))).all())
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(EXPORT_FIELDS)

    def iso(dt) -> str:
        return as_utc(dt).isoformat() if dt else ""

    for u in await session.scalars(select(User).order_by(User.id)):
        writer.writerow([
            u.tg_id, u.source or "organic", u.gender or "", iso(u.created_at), iso(u.activated_at), iso(u.channels_ok_at),
            tg_by_id.get(u.referrer_id, "") if u.referrer_id else "", counted.get(u.id, 0), iso(u.last_seen_at),
            int(bool(u.is_blocked)),
        ])
    return buf.getvalue().encode("utf-8-sig")  # BOM — чтобы Excel не ломал кириллицу


@router.message(Command("export"))
async def cmd_export(message: Message, session: AsyncSession) -> None:
    data = await build_export(session)
    await message.answer_document(
        BufferedInputFile(data, filename="users.csv"),
        caption="Пользователи без даты рождения и имён. referrer_id — Telegram ID пригласившего.",
    )


@router.message(Command("digest"))
async def cmd_digest(message: Message, session: AsyncSession) -> None:
    enabled = await digest.toggle(session, message.from_user.id)
    await message.answer(
        "☀️ Утренний дайджест включён: каждый день в 09:00 МСК." if enabled else "Утренний дайджест выключен."
    )


@router.message(Command("health"))
async def cmd_health(message: Message, session: AsyncSession) -> None:
    h = await health.collect(session)
    last = fmt_dt(h.last_update) if h.last_update else "ещё не было"
    await message.answer("\n".join([
        "🩺 <b>Состояние бота</b>",
        f"Аптайм: <b>{health.human_uptime(h.uptime)}</b>",
        f"Размер базы: <b>{health.human_size(h.db_size) if h.db_size is not None else '—'}</b>",
        f"Последнее обновление: <b>{last}</b>",
        f"Активных каналов: <b>{h.active_channels}</b>",
        f"Пользователей: <b>{h.users}</b>",
        f"Последний бэкап SQLite: <b>{h.last_backup or '—'}</b>",
        f"Удалили свои данные (/deletedata): <b>{h.deleted_data}</b>",
    ]))
