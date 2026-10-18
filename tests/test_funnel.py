"""Блок 4: автоворонка дожима — условия, один раз, лимит 2 в сутки, тихие часы, /stop."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from bot.content import messages as msg
from bot.db.models import Channel, Event, PushLog, Referral, User
from bot.services import daily, funnel, stats
from bot.services.stats import make_period
from tests.conftest import buttons, sent, sent_texts

U = 7001
UTC = timezone.utc
NOON = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)  # 12:00 МСК — вне тихих часов


async def _user(db, tg_id) -> User:
    async with db() as s:
        return await s.scalar(select(User).where(User.tg_id == tg_id))


async def _register(tg, tg_id, birth="07.03.1994"):
    await tg.send(tg_id, "/start")
    await tg.send(tg_id, birth)


async def _event_age(db, tg_id, name, ago: timedelta, now=NOON):
    """Переносим событие name пользователя в прошлое на ago от now."""
    user = await _user(db, tg_id)
    async with db() as s:
        await s.execute(update(Event).where(Event.user_id == user.id, Event.name == name).values(created_at=now - ago))
        await s.commit()


async def _set(db, tg_id, **values):
    async with db() as s:
        await s.execute(update(User).where(User.tg_id == tg_id).values(**values))
        await s.commit()


async def _pushes(db, tg_id=None) -> list[str]:
    async with db() as s:
        stmt = select(PushLog.kind).order_by(PushLog.id)
        if tg_id is not None:
            stmt = stmt.join(User, User.id == PushLog.user_id).where(User.tg_id == tg_id)
        return list(await s.scalars(stmt))


# --- teaser_2h ---

async def test_teaser_push_after_two_hours_once(tg, db):
    await _register(tg, U)
    await _event_age(db, U, "teaser_shown", timedelta(hours=1))
    assert (await funnel.run_funnel(tg.bot, db, NOON))["teaser_2h"] == 0  # рано

    await _event_age(db, U, "teaser_shown", timedelta(hours=3))
    start = len(tg.fake.calls)
    assert (await funnel.run_funnel(tg.bot, db, NOON))["teaser_2h"] == 1
    push = sent(tg.fake.calls[start:], U)[0]
    assert push.text == msg.PUSH_TEASER_NO_CHANNELS
    assert [(b.text, b.callback_data) for b in buttons(push)] == [(msg.BTN_PUSH_OPEN_FULL, "pc:teaser_2h:full")]
    assert (await funnel.run_funnel(tg.bot, db, NOON + timedelta(hours=1)))["teaser_2h"] == 0  # второй раз не уходит
    assert await _pushes(db, U) == ["teaser_2h"]


async def test_teaser_push_mentions_channels_when_configured(tg, db):
    async with db() as s:
        s.add(Channel(chat_id=-1001, title="К", url="https://t.me/k", is_active=True, sort_order=1, joined_count=0))
        await s.commit()
    await _register(tg, U)
    await _event_age(db, U, "teaser_shown", timedelta(hours=3))
    start = len(tg.fake.calls)
    await funnel.run_funnel(tg.bot, db, NOON)
    assert sent(tg.fake.calls[start:], U)[0].text == msg.PUSH_TEASER_CHANNELS


async def test_teaser_push_skipped_when_channels_passed_or_too_old(tg, db):
    await _register(tg, U)
    await _register(tg, U + 1)
    await _event_age(db, U, "teaser_shown", timedelta(hours=3))
    await _event_age(db, U + 1, "teaser_shown", timedelta(days=5))  # слишком давно — не будим старых
    await _set(db, U, channels_ok_at=NOON)
    assert (await funnel.run_funnel(tg.bot, db, NOON))["teaser_2h"] == 0


# --- full_24h ---

async def test_full_push_after_day_without_invites(tg, db):
    await _register(tg, U)
    await tg.click(U, "full")
    await _event_age(db, U, "full_shown", timedelta(hours=25))
    await _event_age(db, U, "teaser_shown", timedelta(minutes=10))  # чтобы не сработал teaser_2h
    start = len(tg.fake.calls)
    result = await funnel.run_funnel(tg.bot, db, NOON)
    assert result["full_24h"] == 1
    push = sent(tg.fake.calls[start:], U)[0]
    assert push.text == msg.PUSH_FULL_24H and buttons(push)[0].callback_data == "pc:full_24h:compat"


async def test_full_push_skipped_when_user_has_invited(tg, db):
    await _register(tg, U)
    await _register(tg, U + 1)
    await tg.click(U, "full")
    await _event_age(db, U, "full_shown", timedelta(hours=25))
    inviter, friend = await _user(db, U), await _user(db, U + 1)
    async with db() as s:
        s.add(Referral(inviter_id=inviter.id, invited_id=friend.id, kind="ref", counted=True))
        await s.commit()
    assert (await funnel.run_funnel(tg.bot, db, NOON))["full_24h"] == 0


# --- inactive_3d ---

async def test_inactive_push_after_three_days(tg, db):
    await _register(tg, U)
    await _register(tg, U + 1)
    await _register(tg, U + 2)
    await _set(db, U, last_seen_at=NOON - timedelta(days=4))
    await _set(db, U + 1, last_seen_at=NOON - timedelta(days=2))   # ещё не пора
    await _set(db, U + 2, last_seen_at=NOON - timedelta(days=30))  # слишком давно
    for uid in (U, U + 1, U + 2):  # teaser_2h в этом тесте не интересует
        await _event_age(db, uid, "teaser_shown", timedelta(minutes=5))
    start = len(tg.fake.calls)
    assert (await funnel.run_funnel(tg.bot, db, NOON))["inactive_3d"] == 1
    pushed = sent(tg.fake.calls[start:])
    assert [m.chat_id for m in pushed] == [U]
    assert pushed[0].text == msg.PUSH_INACTIVE_3D
    assert [b.callback_data for b in buttons(pushed[0])] == ["pc:inactive_3d:today", "pc:inactive_3d:spheres"]


# --- Ограничения ---

async def test_quiet_hours_defer_to_nine_msk(tg, db):
    await _register(tg, U)
    await _event_age(db, U, "teaser_shown", timedelta(hours=10), now=NOON)
    night = datetime(2026, 10, 18, 20, 30, tzinfo=UTC)  # 23:30 МСК
    assert funnel.in_quiet_hours(night) and funnel.in_quiet_hours(datetime(2026, 10, 18, 5, 59, tzinfo=UTC))
    assert not funnel.in_quiet_hours(datetime(2026, 10, 18, 6, 0, tzinfo=UTC))  # 09:00 — уже можно
    assert not funnel.in_quiet_hours(datetime(2026, 10, 18, 19, 59, tzinfo=UTC))  # 22:59
    assert sum((await funnel.run_funnel(tg.bot, db, night)).values()) == 0
    assert await _pushes(db, U) == []
    morning = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)  # 09:00 МСК следующего дня
    assert (await funnel.run_funnel(tg.bot, db, morning))["teaser_2h"] == 1


async def test_at_most_two_pushes_per_day(tg, db):
    await _register(tg, U)
    await tg.click(U, "full")
    # подходят все три триггера сразу
    await _event_age(db, U, "teaser_shown", timedelta(hours=30))
    await _event_age(db, U, "full_shown", timedelta(hours=30))
    await _set(db, U, last_seen_at=NOON - timedelta(days=4), channels_ok_at=None)  # без каналов: подходит и teaser_2h
    result = await funnel.run_funnel(tg.bot, db, NOON)
    assert sum(result.values()) == 2 and len(await _pushes(db, U)) == 2
    assert await funnel.run_funnel(tg.bot, db, NOON + timedelta(hours=2)) == {k: 0 for k in funnel.KINDS}
    # через сутки лимит освободился — уходит оставшийся
    await _event_age(db, U, "teaser_shown", timedelta(hours=30), now=NOON + timedelta(hours=25))  # всё ещё в окне давности
    result = await funnel.run_funnel(tg.bot, db, NOON + timedelta(hours=25))
    assert sum(result.values()) == 1 and len(await _pushes(db, U)) == 3


async def test_daily_push_counts_toward_limit(tg, db):
    await _register(tg, U)
    await _set(db, U, notify_daily=True, notify_hour=9, last_seen_at=NOON - timedelta(days=4))
    await _event_age(db, U, "teaser_shown", timedelta(hours=30))
    await daily.run_daily(tg.bot, db, NOON.replace(hour=6))  # 09:00 МСК
    result = await funnel.run_funnel(tg.bot, db, NOON)
    assert sum(result.values()) == 1  # число дня + один триггер = 2 пуша за сутки


async def test_stop_and_blocked_users_get_nothing(tg, db):
    await _register(tg, U)
    await _register(tg, U + 1)
    for uid in (U, U + 1):
        await _event_age(db, uid, "teaser_shown", timedelta(hours=5))
    await tg.send(U, "/stop")
    await _set(db, U + 1, is_blocked=True)
    assert sum((await funnel.run_funnel(tg.bot, db, NOON)).values()) == 0
    # вернули в /settings — пуш уходит
    await tg.click(U, "mkt:on")
    assert (await funnel.run_funnel(tg.bot, db, NOON))["teaser_2h"] == 1


async def test_users_without_date_get_nothing(tg, db):
    await tg.send(U, "/start")
    await _set(db, U, last_seen_at=NOON - timedelta(days=4))
    assert sum((await funnel.run_funnel(tg.bot, db, NOON)).values()) == 0


async def test_forbidden_marks_blocked_and_counts_blocked_event(tg, db):
    await _register(tg, U)
    await _event_age(db, U, "teaser_shown", timedelta(hours=5))
    tg.fake.forbidden.add(U)
    assert (await funnel.run_funnel(tg.bot, db, NOON))["teaser_2h"] == 0
    assert (await _user(db, U)).is_blocked is True
    async with db() as s:
        m = await stats.mailings(s, make_period("all"))
    assert [(r.kind, r.sent, r.blocked) for r in m.pushes] == [("teaser_2h", 0, 1)]


# --- Клики и друг ---

async def test_push_click_recorded_in_events_and_push_log(tg, db):
    await _register(tg, U)
    await _event_age(db, U, "teaser_shown", timedelta(hours=5))
    await funnel.run_funnel(tg.bot, db, NOON)
    calls = await tg.click(U, "pc:teaser_2h:full")
    assert "полный разбор" in sent_texts(calls)[0]
    async with db() as s:
        row = await s.scalar(select(PushLog).where(PushLog.kind == "teaser_2h"))
        clicks = list(await s.scalars(select(Event).where(Event.name == "push_clicked")))
        m = await stats.mailings(s, make_period("all"))
    assert row.clicked_at is not None and [c.payload for c in clicks] == [{"kind": "teaser_2h"}]
    assert [(r.kind, r.sent, r.clicked) for r in m.pushes] == [("teaser_2h", 1, 1)]


async def test_friend_activation_push_is_logged(tg, db):
    await _register(tg, U)
    code = (await _user(db, U)).ref_code
    await tg.send(U + 1, f"/start r_{code}")
    calls = await tg.send(U + 1, "23.11.1985")
    to_inviter = sent(calls, U)[0]
    assert [b.callback_data for b in buttons(to_inviter)] == ["pc:friend:sphere_money", "pc:friend:invite"]
    inviter, friend = await _user(db, U), await _user(db, U + 1)
    assert await _pushes(db, U) == [f"friend:{friend.id}"]
    async with db() as s:
        sent_events = list(await s.scalars(select(Event.payload).where(Event.name == "push_sent")))
    assert sent_events == [{"kind": "friend"}]
    # нажатие открывает сферу и засчитывается
    opened = await tg.click(U, "pc:friend:sphere_money")
    assert "Деньги" in sent_texts(opened)[0]
    async with db() as s:
        assert (await s.scalar(select(PushLog).where(PushLog.user_id == inviter.id))).clicked_at is not None
