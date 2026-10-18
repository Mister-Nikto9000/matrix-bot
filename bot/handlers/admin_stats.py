"""Отчёты для админа: /stats с кнопками периода и раздела, /sources, /top_inviters."""
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.content import messages as msg
from bot.db.models import User
from bot.handlers.admin import IsAdmin
from bot.services import stats, texts
from bot.services.stats import PERIOD_KEYS, PERIOD_TITLES, human_duration, make_period, pct

router = Router(name="admin_stats")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

SECTIONS = [
    ("summary", "Сводка"), ("funnel", "Воронка"), ("virality", "Вирусность"), ("retention", "Удержание"),
    ("channels", "Каналы"), ("content", "Контент"), ("mailings", "Рассылки"), ("sources", "Источники"),
    ("antifraud", "Антифрод"), ("errors", "Ошибки"),
]
SECTION_TITLES = dict(SECTIONS)
DEFAULT_SECTION, DEFAULT_PERIOD = "summary", "today"
NO_PERIOD = {"retention", "errors"}  # эти разделы считаются по фиксированному окну

SPHERE_NAMES = {key: title for key, _, _, title in msg.SPHERE_BUTTONS}
INLINE_NAMES = {"arcana": msg.INLINE_ARCANA_TITLE, "day": msg.INLINE_DAY_TITLE, "compat": msg.INLINE_COMPAT_TITLE,
                "need_date": "без даты"}


def user_label(user: User) -> str:
    name = escape(user.first_name or "—")
    tag = f" @{escape(user.username)}" if user.username else ""
    return f"{name}{tag}"


def stats_kb(section: str, period: str) -> InlineKeyboardMarkup:
    def mark(text: str, active: bool) -> str:
        return f"• {text}" if active else text

    periods = [InlineKeyboardButton(text=mark(PERIOD_TITLES[k], k == period), callback_data=f"st:{section}:{k}")
               for k in PERIOD_KEYS]
    rows = [periods[:3], periods[3:]]
    buttons = [InlineKeyboardButton(text=mark(title, key == section), callback_data=f"st:{key}:{period}")
               for key, title in SECTIONS]
    rows += [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- Разделы ---

async def render_summary(session: AsyncSession, p) -> str:
    s = await stats.summary(session, p)
    with_gender = s.females + s.males
    lines = [
        f"📊 <b>Сводка — {p.title}</b>",
        f"Новые: <b>{s.new}</b>",
        f"Активированные: <b>{s.activated}</b> ({pct(s.activated, s.new)} от новых)",
        f"Заблокировали бота: <b>{s.blocked}</b> ({pct(s.blocked, s.new)} от новых)",
        f"DAU / WAU / MAU: <b>{s.dau} / {s.wau} / {s.mau}</b>",
        f"Пол: 👩 {pct(s.females, with_gender)} · 👨 {pct(s.males, with_gender)} <i>(из {with_gender} указавших)</i>",
        "",
        "<b>Динамика за 14 дней</b>",
        f"Новые:   <code>{s.spark_new}</code>",
        f"Активир.: <code>{s.spark_activated}</code>",
        "",
        "<b>Ошибки за 7 дней:</b> " + (", ".join(f"{d:%d.%m} — {n}" for d, n in s.errors_by_day if n) or "нет"),
    ]
    return "\n".join(lines)


async def render_funnel(session: AsyncSession, p) -> str:
    lines = [f"🔻 <b>Воронка — {p.title}</b>", "<i>уникальные пользователи на шаге; % от предыдущего / от старта</i>", ""]
    for i, step in enumerate(await stats.funnel(session, p), 1):
        tail = "" if i == 1 else f" — {step.of_prev} / {step.of_start}"
        lines.append(f"{i}. {step.title}: <b>{step.count}</b>{tail}")
    return "\n".join(lines)


async def render_virality(session: AsyncSession, p) -> str:
    v = await stats.virality(session, p)
    median = human_duration(v.median_first_friend) if v.median_first_friend is not None else "—"
    return "\n".join([
        f"🔁 <b>Вирусность — {p.title}</b>",
        f"<b>K = {v.k_total:.2f}</b> (реферальные активации {v.acts_ref + v.acts_comp} / активированные {v.activated})",
        f"K по ссылкам r_: {v.k_ref:.2f} ({v.acts_ref})",
        f"K по ссылкам c_: {v.k_comp:.2f} ({v.acts_comp})",
        f"Приглашений на приглашающего: <b>{v.invites_per_opener:.2f}</b> <i>(среди {v.openers} открывших «Пригласить»)</i>",
        f"Старт → активация по приглашениям: <b>{pct(v.invited_activated, v.invited_users)}</b> ({v.invited_activated} из {v.invited_users})",
        f"Пришли по рефералке: <b>{pct(v.invited_users, v.new_users)}</b> от новых",
        f"Медиана до первого активированного друга: <b>{median}</b>",
        f"Inline-шеринг (выбрали карточку): <b>{v.inline_shared}</b>"
        + (f" ({', '.join(f'{INLINE_NAMES.get(k, k)} — {n}' for k, n in v.inline_variants.most_common())})" if v.inline_shared else ""),
        "",
        "Топ приглашающих — /top_inviters",
    ])


async def render_retention(session: AsyncSession, p) -> str:
    table, total = await stats.retention_table(session, 14)

    def cell(x: float | None) -> str:
        return "  —" if x is None else f"{x:>3.0%}"

    rows = ["день    n    D1  D7  D30"]
    rows += [f"{r.day:%d.%m} {r.size:>4}  {cell(r.d1)} {cell(r.d7)} {cell(r.d30)}" for r in table]
    return "\n".join([
        "📆 <b>Удержание</b> <i>(когорты по дню регистрации, последние 14)</i>",
        f"Итого: D1 <b>{pct_or_dash(total[1])}</b> · D7 <b>{pct_or_dash(total[7])}</b> · D30 <b>{pct_or_dash(total[30])}</b>",
        "<i>«—» — срок ещё не наступил</i>",
        "",
        f"<pre>{chr(10).join(rows)}</pre>",
    ])


pct_or_dash = stats.pct_of


async def render_channels(session: AsyncSession, p) -> str:
    rows = await stats.channel_stats(session, p)
    if not rows:
        return f"📣 <b>Каналы — {p.title}</b>\nКаналов пока нет."
    lines = [f"📣 <b>Каналы — {p.title}</b>"]
    for r in rows:
        state = "" if r.channel.is_active else " <i>(выключен)</i>"
        lines.append(
            f"\n<b>#{r.channel.id} {escape(r.channel.title)}</b>{state}\n"
            f"показан: {r.shown} · прошли: {r.passed} · конверсия: {r.conversion}\n"
            f"заявок: {r.requests} · застряли &gt;24 ч: {r.stuck}"
        )
    return "\n".join(lines)


async def render_content(session: AsyncSession, p) -> str:
    c = await stats.content(session, p)
    spheres = ", ".join(f"{SPHERE_NAMES.get(k, k)} — {n}" for k, n in c.spheres.most_common()) or "пока нет"

    def top(items: list[tuple[int, int]]) -> str:
        return ", ".join(f"{n}. {texts.name(n)} — {cnt}" for n, cnt in items) or "нет данных"

    decades = ", ".join(f"{d}-е — {n}" for d, n in sorted(c.decades.items())) or "нет данных"
    return "\n".join([
        f"🧩 <b>Контент — {p.title}</b>",
        f"<b>Открытия сфер:</b> {spheres}",
        f"<b>Топ арканов личности:</b> {top(c.top_personality)}",
        f"<b>Топ арканов предназначения:</b> {top(c.top_purpose)}",
        f"<b>Десятилетия рождения:</b> {decades}",
        f"<b>Совместимость:</b> ссылок создано {c.compat_links}, завершённых пар {c.compat_pairs} "
        f"({pct(c.compat_pairs, c.compat_links)})",
    ])


async def render_mailings(session: AsyncSession, p) -> str:
    m = await stats.mailings(session, p)
    lines = [
        f"✉️ <b>Рассылки — {p.title}</b>",
        f"Подписаны на число дня: <b>{m.daily_subscribers}</b>",
        f"Отключили маркетинговые сообщения: <b>{m.marketing_off}</b>",
        "",
        "<b>Автопуши</b> (отправлено / заблокировали / нажатий):",
    ]
    lines += [f"• {r.kind}: {r.sent} / {r.blocked} / {r.clicked} ({pct(r.clicked, r.sent)})" for r in m.pushes] or ["нет отправок"]
    lines += render_mailings_extra(m)
    return "\n".join(lines)


def render_mailings_extra(m: stats.Mailings) -> list[str]:
    """Рассылки админа и квесты — подключаются в своих блоках."""
    lines = []
    if m.broadcasts:
        lines += ["", "<b>Рассылки админа:</b>"]
        lines += [
            f"• #{b.id} {b.created_at:%d.%m %H:%M}: отправлено {b.sent}, заблокировали {b.blocked}, ошибки {b.failed}"
            for b in m.broadcasts
        ]
    if m.quests:
        q = m.quests
        lines += ["", f"<b>Квесты:</b> показано {q['shown']}, выполнено {q['done']} ({pct(q['done'], q['shown'])})"]
    return lines


async def render_sources(session: AsyncSession, p) -> str:
    rows = await stats.source_stats(session, p)
    if not rows:
        return f"🧭 <b>Источники — {p.title}</b>\nНет пользователей за период."
    lines = [f"🧭 <b>Источники — {p.title}</b>", "<i>пользователи · активация · прошли каналы · K · D1</i>", ""]
    for r in rows[:20]:
        lines.append(
            f"<b>{escape(r.source)}</b>: {r.users} · {pct(r.activated, r.users)} · {pct(r.channels_ok, r.users)} · "
            f"{r.k:.2f} · {pct_or_dash(r.d1)}"
        )
    return "\n".join(lines)


async def render_antifraud(session: AsyncSession, p) -> str:
    v = await stats.virality(session, p)
    lines = [
        f"🛡 <b>Антифрод — {p.title}</b>",
        f"Незасчитанные активации: <b>{v.uncounted}</b>",
        f"Аккаунты без username: <b>{v.no_username}</b> ({pct(v.no_username, v.new_users)} от новых)",
        f"Приглашающие с &gt;{stats.HEAVY_INVITER_LIMIT} активаций за сутки: <b>{len(v.heavy_inviters)}</b>",
    ]
    for uid, day, n in v.heavy_inviters[:10]:
        user = await session.get(User, uid)
        lines.append(f"• {user_label(user) if user else uid} — {n} за {day:%d.%m}")
    return "\n".join(lines)


async def render_errors(session: AsyncSession, p) -> str:
    days = await stats.error_counts(session, 14)
    lines = ["⚠️ <b>Необработанные ошибки по дням</b> <i>(14 дней)</i>"]
    lines += [f"{d:%d.%m}: {n}" for d, n in days if n] or ["Ошибок нет 🎉"]
    return "\n".join(lines)


RENDERERS = {
    "summary": render_summary, "funnel": render_funnel, "virality": render_virality, "retention": render_retention,
    "channels": render_channels, "content": render_content, "mailings": render_mailings, "sources": render_sources,
    "antifraud": render_antifraud, "errors": render_errors,
}


async def render_report(session: AsyncSession, section: str, period_key: str) -> tuple[str, InlineKeyboardMarkup]:
    section = section if section in RENDERERS else DEFAULT_SECTION
    period_key = period_key if period_key in PERIOD_KEYS else DEFAULT_PERIOD
    text = await RENDERERS[section](session, make_period(period_key))
    return text, stats_kb(section, period_key)


@router.message(Command("stats"))
async def cmd_stats(message: Message, session: AsyncSession) -> None:
    text, markup = await render_report(session, DEFAULT_SECTION, DEFAULT_PERIOD)
    await message.answer(text, reply_markup=markup)


@router.message(Command("sources"))
async def cmd_sources(message: Message, session: AsyncSession) -> None:
    text, markup = await render_report(session, "sources", "30d")
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("st:"))
async def cb_stats(call: CallbackQuery, session: AsyncSession) -> None:
    _, section, period_key = call.data.split(":", 2)
    text, markup = await render_report(session, section, period_key)
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:  # «message is not modified» — нажали уже открытую кнопку
        pass
    await call.answer()


@router.message(Command("top_inviters"))
async def cmd_top_inviters(message: Message, session: AsyncSession) -> None:
    top = await stats.top_inviters(session, 10)
    if not top:
        await message.answer("Приглашающих пока нет.")
        return
    lines = ["🏆 <b>Топ-10 приглашающих</b>", "<i>засчитанные / не засчитанные</i>", ""]
    lines += [f"{i}. {user_label(r.user)} — <b>{r.counted}</b> / {r.uncounted}" for i, r in enumerate(top, 1)]
    await message.answer("\n".join(lines))
