from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from bot.db.models import Event, Referral, User
from bot.handlers.admin import collect_stats
from tests.conftest import sent_texts

ADMIN, A, B, C = 999, 4001, 4002, 4003


async def test_stats_counts_and_k_factor(tg, db):
    await tg.send(A, "/start")
    await tg.send(A, "07.03.1994")
    await tg.send(A, "/invite")
    async with db() as s:
        code = (await s.scalar(select(User).where(User.tg_id == A))).ref_code
    await tg.send(B, f"/start r_{code}")
    await tg.send(B, "23.11.1985")
    await tg.send(C, f"/start r_{code}")  # старт без активации

    async with db() as s:
        st = await collect_stats(s, None)
    assert (st.new_users, st.activated, st.invite_opened, st.ref_started, st.ref_activated, st.ref_users) == (3, 2, 1, 2, 1, 2)
    assert st.k_factor == 0.5
    assert round(st.ref_share, 2) == 0.67

    calls = await tg.send(ADMIN, "/stats")
    text = sent_texts(calls)[0]
    assert "Сегодня (UTC)" in text and "7 дней" in text and "Всё время" in text
    assert "K = 0.50" in text


async def test_stats_period_filter(tg, db):
    old = datetime.now(timezone.utc) - timedelta(days=30)
    async with db() as s:
        u = User(tg_id=1, ref_code="old00001", created_at=old, activated_at=old, is_blocked=False)
        s.add(u)
        await s.commit()
        week = await collect_stats(s, datetime.now(timezone.utc) - timedelta(days=7))
        total = await collect_stats(s, None)
    assert (week.new_users, week.activated, week.k_factor) == (0, 0, 0.0)
    assert (total.new_users, total.activated) == (1, 1)


async def test_all_spec_events_are_recorded(tg, db):
    """Все события из раздела 8 ТЗ пишутся при обычном пользовательском пути."""
    from bot.db.models import Channel
    async with db() as s:
        s.add(Channel(chat_id=-1001, title="К", url="https://t.me/k", is_active=True, sort_order=1, joined_count=0))
        await s.commit()
    await tg.send(A, "/start")
    await tg.send(A, "07.03.1994")
    await tg.click(A, "full")
    tg.fake.members[(-1001, A)] = "member"
    await tg.click(A, "check_subs")
    await tg.click(A, "invite")
    await tg.click(A, "share:r")
    await tg.click(A, "compat")
    async with db() as s:
        code = (await s.scalar(select(User).where(User.tg_id == A))).ref_code
    await tg.send(B, f"/start r_{code}")
    await tg.send(B, "23.11.1985")
    await tg.click(B, "sphere:money")
    async with db() as s:
        names = set(await s.scalars(select(Event.name)))
    expected = {"start", "birth_date_set", "teaser_shown", "channels_prompt", "channels_ok", "full_shown",
                "invite_opened", "share_clicked", "compat_link_created", "referral_started",
                "referral_activated", "unlock", "sphere_opened"}
    assert expected <= names, expected - names
