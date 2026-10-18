"""Метрики для админа. Чистый расчёт по БД, без aiogram: отчёты собирает handlers/admin_stats.py.

Все периоды считаются по московским суткам и переводятся в UTC для запросов (SQLite хранит UTC-часы).
Точные даты рождения нигде не выводятся: только агрегаты (десятилетия, арканы).
"""
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import (
    Channel,
    CompatPair,
    DailyActivity,
    ErrorLog,
    Event,
    JoinRequest,
    Quest,
    Referral,
    User,
)
from bot.services.clock import as_utc, day_range_utc, day_start_utc, msk_day, msk_today, now_utc
from bot.services.matrix_calc import calculate

SPARK_CHARS = "▁▂▃▄▅▆▇█"
PERIOD_KEYS = ("today", "yesterday", "7d", "30d", "all")
PERIOD_TITLES = {"today": "Сегодня", "yesterday": "Вчера", "7d": "7 дней", "30d": "30 дней", "all": "Всё время"}
HEAVY_INVITER_LIMIT = 10  # больше столько активаций за сутки — подозрительно


# --- Периоды ---

@dataclass(frozen=True)
class Period:
    key: str
    since: datetime | None  # UTC, включительно; None — с начала времён
    until: datetime | None  # UTC, не включительно; None — до сейчас

    @property
    def title(self) -> str:
        return PERIOD_TITLES[self.key]

    def clauses(self, column) -> list:
        out = []
        if self.since is not None:
            out.append(column >= self.since)
        if self.until is not None:
            out.append(column < self.until)
        return out

    def anchor_day(self, now: datetime | None = None) -> date:
        """Последний день периода по Москве — от него считаются DAU/WAU/MAU."""
        if self.until is not None:
            return msk_day(self.until - timedelta(seconds=1))
        return msk_today(now)


def make_period(key: str, now: datetime | None = None) -> Period:
    today = msk_today(now)
    if key == "today":
        return Period(key, day_start_utc(today), None)
    if key == "yesterday":
        return Period(key, day_start_utc(today - timedelta(days=1)), day_start_utc(today))
    if key == "7d":
        return Period(key, day_start_utc(today - timedelta(days=6)), None)
    if key == "30d":
        return Period(key, day_start_utc(today - timedelta(days=29)), None)
    return Period("all", None, None)


# --- Мелкие помощники ---

def pct(part: int | float, whole: int | float) -> str:
    return f"{part / whole:.0%}" if whole else "—"


def pct_of(x: float | None) -> str:
    return "—" if x is None else f"{x:.0%}"


def ratio(part: int | float, whole: int | float) -> float:
    return part / whole if whole else 0.0


def sparkline(values: list[int]) -> str:
    top = max(values, default=0)
    if top <= 0:
        return SPARK_CHARS[0] * len(values)
    return "".join(SPARK_CHARS[min(len(SPARK_CHARS) - 1, round(v / top * (len(SPARK_CHARS) - 1)))] for v in values)


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 3600:
        return f"{max(seconds // 60, 0)} мин"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} ч"
    return f"{seconds / 86400:.1f} дн"


async def _count(session: AsyncSession, stmt) -> int:
    return await session.scalar(stmt) or 0


async def _events(session: AsyncSession, name: str, p: Period) -> list[Event]:
    stmt = select(Event).where(Event.name == name, *p.clauses(Event.created_at))
    return list(await session.scalars(stmt))


async def _distinct_event_users(session: AsyncSession, name: str, p: Period) -> int:
    stmt = select(func.count(distinct(Event.user_id))).where(Event.name == name, *p.clauses(Event.created_at))
    return await _count(session, stmt)


# --- Сводка ---

@dataclass
class Summary:
    new: int = 0
    activated: int = 0
    blocked: int = 0
    dau: int = 0
    wau: int = 0
    mau: int = 0
    females: int = 0
    males: int = 0
    spark_new: str = ""
    spark_activated: str = ""
    errors_by_day: list[tuple[date, int]] = field(default_factory=list)


async def daily_counts(session: AsyncSession, column, days: int = 14, now: datetime | None = None, *extra) -> list[int]:
    """Сколько записей по колонке-времени в каждые из последних `days` московских суток (старые → новые)."""
    today = msk_today(now)
    first = today - timedelta(days=days - 1)
    rows = await session.scalars(select(column).where(column >= day_start_utc(first), *extra))
    counter = Counter(msk_day(r) for r in rows)
    return [counter.get(first + timedelta(days=i), 0) for i in range(days)]


async def active_users(session: AsyncSession, anchor: date, days: int) -> int:
    """Уникальные активные за `days` суток, заканчивая днём anchor."""
    stmt = select(func.count(distinct(DailyActivity.user_id))).where(
        DailyActivity.day > anchor - timedelta(days=days), DailyActivity.day <= anchor
    )
    return await _count(session, stmt)


async def error_counts(session: AsyncSession, days: int = 7, now: datetime | None = None) -> list[tuple[date, int]]:
    counts = await daily_counts(session, ErrorLog.created_at, days, now)
    today = msk_today(now)
    return [(today - timedelta(days=days - 1 - i), n) for i, n in enumerate(counts)]


async def summary(session: AsyncSession, p: Period, now: datetime | None = None) -> Summary:
    new_q = select(func.count()).select_from(User).where(*p.clauses(User.created_at))
    anchor = p.anchor_day(now)
    gender_q = select(User.gender, func.count()).where(*p.clauses(User.created_at), User.gender.is_not(None)).group_by(
        User.gender
    )
    genders = dict((await session.execute(gender_q)).all())
    return Summary(
        new=await _count(session, new_q),
        activated=await _count(session, select(func.count()).select_from(User).where(User.activated_at.is_not(None), *p.clauses(User.activated_at))),
        blocked=await _count(session, new_q.where(User.is_blocked.is_(True))),
        dau=await active_users(session, anchor, 1),
        wau=await active_users(session, anchor, 7),
        mau=await active_users(session, anchor, 30),
        females=genders.get("f", 0),
        males=genders.get("m", 0),
        spark_new=sparkline(await daily_counts(session, User.created_at, 14, now)),
        spark_activated=sparkline(await daily_counts(session, User.activated_at, 14, now)),
        errors_by_day=await error_counts(session, 7, now),
    )


# --- Воронка ---

FUNNEL_STEPS = [
    ("start", "Нажали /start"),
    ("birth_date_set", "Ввели дату"),
    ("teaser_shown", "Увидели тизер"),
    ("full_clicked", "Нажали «Полный разбор»"),
    ("channels_ok", "Прошли каналы"),
    ("full_shown", "Увидели разбор"),
    ("invite_opened", "Открыли «Пригласить»"),
    ("share_clicked", "Нажали «Поделиться»"),
    ("referral_started", "Реферальный старт"),
    ("referral_activated", "Реферальная активация"),
]


@dataclass
class FunnelStep:
    title: str
    count: int
    of_prev: str
    of_start: str


async def funnel(session: AsyncSession, p: Period) -> list[FunnelStep]:
    steps, start, prev = [], 0, 0
    for i, (name, title) in enumerate(FUNNEL_STEPS):
        n = await _distinct_event_users(session, name, p)
        if i == 0:
            start = n
        steps.append(FunnelStep(title, n, "" if i == 0 else pct(n, prev), "" if i == 0 else pct(n, start)))
        prev = n
    return steps


# --- Вирусность ---

@dataclass
class Virality:
    activated: int = 0
    acts_ref: int = 0
    acts_comp: int = 0
    invites_per_opener: float = 0.0
    openers: int = 0
    invited_users: int = 0
    invited_activated: int = 0
    new_users: int = 0
    median_first_friend: float | None = None
    inline_shared: int = 0
    uncounted: int = 0
    heavy_inviters: list[tuple[int, date, int]] = field(default_factory=list)  # (user_id, день, активаций)
    no_username: int = 0

    @property
    def k_total(self) -> float:
        return ratio(self.acts_ref + self.acts_comp, self.activated)

    @property
    def k_ref(self) -> float:
        return ratio(self.acts_ref, self.activated)

    @property
    def k_comp(self) -> float:
        return ratio(self.acts_comp, self.activated)


async def virality(session: AsyncSession, p: Period) -> Virality:
    v = Virality()
    v.activated = await _count(session, select(func.count()).select_from(User).where(User.activated_at.is_not(None), *p.clauses(User.activated_at)))
    v.new_users = await _count(session, select(func.count()).select_from(User).where(*p.clauses(User.created_at)))
    acts = select(func.count()).select_from(Referral).where(Referral.activated_at.is_not(None), *p.clauses(Referral.activated_at))
    v.acts_ref = await _count(session, acts.where(Referral.kind == "ref"))
    v.acts_comp = await _count(session, acts.where(Referral.kind == "comp"))

    openers = set(await session.scalars(
        select(Event.user_id).distinct().where(Event.name == "invite_opened", *p.clauses(Event.created_at))
    ))
    v.openers = len(openers)
    if openers:
        invites = await session.scalars(select(Referral.inviter_id).where(*p.clauses(Referral.created_at)))
        v.invites_per_opener = sum(1 for i in invites if i in openers) / len(openers)

    invited = select(func.count()).select_from(User).where(User.referrer_id.is_not(None), *p.clauses(User.created_at))
    v.invited_users = await _count(session, invited)
    v.invited_activated = await _count(session, invited.where(User.activated_at.is_not(None)))

    first_friend = (await session.execute(
        select(Referral.inviter_id, func.min(Referral.activated_at)).where(
            Referral.counted.is_(True), Referral.activated_at.is_not(None), *p.clauses(Referral.activated_at)
        ).group_by(Referral.inviter_id)
    )).all()
    deltas = []
    for inviter_id, first_at in first_friend:
        inviter_act = await session.scalar(select(User.activated_at).where(User.id == inviter_id))
        if inviter_act is not None and first_at is not None:
            deltas.append(max((as_utc(first_at) - as_utc(inviter_act)).total_seconds(), 0))
    v.median_first_friend = statistics.median(deltas) if deltas else None

    v.inline_shared = await _count(
        session, select(func.count()).select_from(Event).where(Event.name == "inline_shared", *p.clauses(Event.created_at))
    )

    # Антифрод
    v.uncounted = await _count(session, select(func.count()).select_from(Referral).where(
        Referral.activated_at.is_not(None), Referral.counted.is_(False), *p.clauses(Referral.activated_at)
    ))
    per_day: Counter = Counter()
    for inviter_id, at in (await session.execute(
        select(Referral.inviter_id, Referral.activated_at).where(Referral.activated_at.is_not(None), *p.clauses(Referral.activated_at))
    )).all():
        per_day[(inviter_id, msk_day(at))] += 1
    v.heavy_inviters = sorted(
        ((uid, day, n) for (uid, day), n in per_day.items() if n > HEAVY_INVITER_LIMIT), key=lambda r: -r[2]
    )
    v.no_username = await _count(
        session, select(func.count()).select_from(User).where(User.username.is_(None), *p.clauses(User.created_at))
    )
    return v


@dataclass
class Inviter:
    user: User
    counted: int
    uncounted: int


async def top_inviters(session: AsyncSession, limit: int = 10) -> list[Inviter]:
    counted = func.sum(case((Referral.counted.is_(True), 1), else_=0))
    uncounted = func.sum(case((Referral.counted.is_(False), 1), else_=0))
    rows = (await session.execute(
        select(Referral.inviter_id, counted, uncounted)
        .where(Referral.activated_at.is_not(None))
        .group_by(Referral.inviter_id)
        .order_by(counted.desc(), uncounted.desc())
        .limit(limit)
    )).all()
    out = []
    for inviter_id, c, u in rows:
        user = await session.get(User, inviter_id)
        if user is not None:
            out.append(Inviter(user, int(c or 0), int(u or 0)))
    return out


# --- Удержание ---

@dataclass
class CohortRow:
    day: date
    size: int
    d1: float | None  # None — срок ещё не наступил
    d7: float | None
    d30: float | None


RETENTION_DAYS = (1, 7, 30)


def retention_for(users: list[tuple[int, date]], active: set[tuple[int, date]], today: date) -> dict[int, float | None]:
    """D1/D7/D30 для набора (user_id, день регистрации): доля, вернувшаяся ровно на N-й день.

    Пользователи, для которых N-й день ещё не наступил, в расчёт не входят.
    """
    out: dict[int, float | None] = {}
    for n in RETENTION_DAYS:
        eligible = [(uid, d) for uid, d in users if d + timedelta(days=n) <= today]
        if not eligible:
            out[n] = None
            continue
        back = sum(1 for uid, d in eligible if (uid, d + timedelta(days=n)) in active)
        out[n] = back / len(eligible)
    return out


async def _activity_of(session: AsyncSession, since_day: date) -> set[tuple[int, date]]:
    """Активность пользователей, зарегистрированных не раньше since_day (московский день)."""
    first_utc = day_start_utc(since_day)
    ids = select(User.id).where(User.created_at >= first_utc)
    rows = await session.execute(select(DailyActivity.user_id, DailyActivity.day).where(DailyActivity.user_id.in_(ids)))
    return {(uid, d) for uid, d in rows.all()}


async def retention_table(session: AsyncSession, cohorts: int = 14, now: datetime | None = None) -> tuple[list[CohortRow], dict]:
    today = msk_today(now)
    first = today - timedelta(days=cohorts - 1)
    rows = (await session.execute(select(User.id, User.created_at).where(User.created_at >= day_start_utc(first)))).all()
    by_day: dict[date, list[tuple[int, date]]] = defaultdict(list)
    for uid, created in rows:
        d = msk_day(created)
        by_day[d].append((uid, d))
    active = await _activity_of(session, first)
    table = []
    for i in range(cohorts):
        day = first + timedelta(days=i)
        r = retention_for(by_day.get(day, []), active, today)
        table.append(CohortRow(day, len(by_day.get(day, [])), r[1], r[7], r[30]))
    everyone = [u for users in by_day.values() for u in users]
    return table, retention_for(everyone, active, today)


async def cohort_d1(session: AsyncSession, day: date, now: datetime | None = None) -> float | None:
    """D1 когорты, зарегистрировавшейся в московский день `day`."""
    start, end = day_range_utc(day)
    users = [(uid, day) for (uid,) in (await session.execute(
        select(User.id).where(User.created_at >= start, User.created_at < end))).all()]
    if not users:
        return None
    ids = [uid for uid, _ in users]
    active = {(uid, d) for uid, d in (await session.execute(
        select(DailyActivity.user_id, DailyActivity.day).where(
            DailyActivity.user_id.in_(ids), DailyActivity.day == day + timedelta(days=1)))).all()}
    return retention_for(users, active, msk_today(now) + timedelta(days=1))[1]


# --- Каналы ---

@dataclass
class ChannelRow:
    channel: Channel
    shown: int
    passed: int
    requests: int
    stuck: int

    @property
    def conversion(self) -> str:
        return pct(self.passed, self.shown)


async def channel_stats(session: AsyncSession, p: Period, now: datetime | None = None) -> list[ChannelRow]:
    now = now or now_utc()
    channels = list(await session.scalars(select(Channel).order_by(Channel.sort_order, Channel.id)))
    shown, passed = Counter(), Counter()
    for ev in await _events(session, "channels_prompt", p):
        for cid in (ev.payload or {}).get("missing", []):
            shown[cid] += 1
    for ev in await _events(session, "channels_ok", p):
        for cid in (ev.payload or {}).get("channels", []):
            passed[cid] += 1

    # «Застряли»: показали подписку больше суток назад, а каналы так и не пройдены
    stuck: dict[int, set[int]] = defaultdict(set)
    limit = now - timedelta(hours=24)
    prompts = (await session.execute(
        select(Event.user_id, Event.payload).join(User, User.id == Event.user_id).where(
            Event.name == "channels_prompt", Event.created_at < limit, User.channels_ok_at.is_(None))
    )).all()
    for uid, payload in prompts:
        for cid in (payload or {}).get("missing", []):
            stuck[cid].add(uid)

    out = []
    for ch in channels:
        requests = await _count(session, select(func.count()).select_from(JoinRequest).where(
            JoinRequest.chat_id == ch.chat_id, *p.clauses(JoinRequest.created_at)))
        out.append(ChannelRow(ch, shown[ch.id], passed[ch.id], requests, len(stuck[ch.id])))
    return out


# --- Контент ---

@dataclass
class Content:
    spheres: Counter = field(default_factory=Counter)
    top_personality: list[tuple[int, int]] = field(default_factory=list)
    top_purpose: list[tuple[int, int]] = field(default_factory=list)
    decades: Counter = field(default_factory=Counter)
    compat_links: int = 0
    compat_pairs: int = 0


async def content(session: AsyncSession, p: Period) -> Content:
    c = Content()
    for ev in await _events(session, "sphere_opened", p):
        c.spheres[(ev.payload or {}).get("sphere", "?")] += 1
    personality, purpose = Counter(), Counter()
    births = await session.scalars(select(User.birth_date).where(User.birth_date.is_not(None), User.activated_at.is_not(None), *p.clauses(User.activated_at)))
    for birth in births:
        m = calculate(birth)
        personality[m.a] += 1
        purpose[m.purpose] += 1
        c.decades[birth.year // 10 * 10] += 1
    c.top_personality = personality.most_common(5)
    c.top_purpose = purpose.most_common(5)
    c.compat_links = await _count(session, select(func.count()).select_from(Event).where(
        Event.name == "compat_link_created", *p.clauses(Event.created_at)))
    c.compat_pairs = await _count(session, select(func.count()).select_from(CompatPair).where(
        *p.clauses(CompatPair.created_at)))
    return c


# --- Источники ---

@dataclass
class SourceRow:
    source: str
    users: int
    activated: int
    channels_ok: int
    ref_activations: int
    d1: float | None

    @property
    def k(self) -> float:
        return ratio(self.ref_activations, self.activated)


async def source_stats(session: AsyncSession, p: Period, now: datetime | None = None) -> list[SourceRow]:
    today = msk_today(now)
    rows = (await session.execute(
        select(User.id, func.coalesce(User.source, "organic"), User.created_at, User.activated_at, User.channels_ok_at)
        .where(*p.clauses(User.created_at))
    )).all()
    by_source: dict[str, list] = defaultdict(list)
    for r in rows:
        by_source[r[1]].append(r)
    if not rows:
        return []
    active = await _activity_of(session, min(msk_day(r[2]) for r in rows))
    ref_acts = Counter(dict((await session.execute(
        select(Referral.inviter_id, func.count()).where(Referral.activated_at.is_not(None)).group_by(Referral.inviter_id)
    )).all()))
    out = []
    for source, items in by_source.items():
        d1 = retention_for([(r[0], msk_day(r[2])) for r in items], active, today)[1]
        out.append(SourceRow(
            source=source, users=len(items), activated=sum(1 for r in items if r[3] is not None),
            channels_ok=sum(1 for r in items if r[4] is not None),
            ref_activations=sum(ref_acts[r[0]] for r in items), d1=d1,
        ))
    return sorted(out, key=lambda r: -r.users)


# --- Рассылки ---

@dataclass
class PushRow:
    kind: str
    sent: int
    blocked: int
    clicked: int

    @property
    def delivered(self) -> int:
        return self.sent  # push_sent пишется только после успешной отправки


@dataclass
class Mailings:
    daily_subscribers: int = 0
    marketing_off: int = 0
    pushes: list[PushRow] = field(default_factory=list)
    broadcasts: list = field(default_factory=list)
    quests: dict = field(default_factory=dict)


async def mailings(session: AsyncSession, p: Period) -> Mailings:
    m = Mailings()
    m.daily_subscribers = await _count(session, select(func.count()).select_from(User).where(
        User.notify_daily.is_(True), User.is_blocked.is_(False)))
    m.marketing_off = await _count(session, select(func.count()).select_from(User).where(User.notify_marketing.is_(False)))
    sent, blocked, clicked = Counter(), Counter(), Counter()
    for name, counter in (("push_sent", sent), ("push_blocked", blocked), ("push_clicked", clicked)):
        for ev in await _events(session, name, p):
            counter[(ev.payload or {}).get("kind", "?")] += 1
    for kind in sorted(set(sent) | set(blocked) | set(clicked)):
        m.pushes.append(PushRow(kind, sent[kind], blocked[kind], clicked[kind]))
    m.quests = await quest_stats(session, p)
    return m


async def quest_stats(session: AsyncSession, p: Period) -> dict:
    """Квесты за период: показано (создано на день) и выполнено."""
    clauses = []
    if p.since is not None:
        clauses.append(Quest.day >= msk_day(p.since))
    if p.until is not None:
        clauses.append(Quest.day < msk_day(p.until))
    base = select(func.count()).select_from(Quest).where(*clauses)
    shown = await _count(session, base)
    if not shown:
        return {}
    return {"shown": shown, "done": await _count(session, base.where(Quest.completed_at.is_not(None)))}


# --- Ошибки ---

async def errors_in_last_hour(session: AsyncSession, now: datetime | None = None) -> int:
    since = (now or now_utc()) - timedelta(hours=1)
    return await _count(session, select(func.count()).select_from(ErrorLog).where(ErrorLog.created_at >= since))


# --- Данные для дайджеста и алертов ---

@dataclass
class DayDigest:
    new: int
    activated: int
    k: float
    ref_share: float
    channels_ok: int
    errors: int


async def day_digest(session: AsyncSession, day: date) -> DayDigest:
    start, end = day_range_utc(day)
    p = Period("custom", start, end)
    v = await virality(session, p)
    return DayDigest(
        new=v.new_users,
        activated=v.activated,
        k=v.k_total,
        ref_share=ratio(v.invited_users, v.new_users),
        channels_ok=await _count(session, select(func.count()).select_from(User).where(User.channels_ok_at.is_not(None), *p.clauses(User.channels_ok_at))),
        errors=await _count(session, select(func.count()).select_from(ErrorLog).where(*p.clauses(ErrorLog.created_at))),
    )


async def k_window(session: AsyncSession, since: datetime, until: datetime) -> tuple[float, int]:
    """(K, число активированных) за произвольное окно."""
    v = await virality(session, Period("custom", since, until))
    return v.k_total, v.activated
