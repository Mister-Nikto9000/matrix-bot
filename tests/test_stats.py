from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from bot.db.models import Event, User
from bot.services import stats
from bot.services.stats import make_period
from tests.conftest import sent_texts

ADMIN, A, B, C = 999, 4001, 4002, 4003


async def test_virality_counts_and_k_factor(tg, db):
    await tg.send(A, "/start")
    await tg.send(A, "07.03.1994")
    await tg.send(A, "/invite")
    async with db() as s:
        code = (await s.scalar(select(User).where(User.tg_id == A))).ref_code
    await tg.send(B, f"/start r_{code}")
    await tg.send(B, "23.11.1985")
    await tg.send(C, f"/start r_{code}")  # старт без активации

    async with db() as s:
        v = await stats.virality(s, make_period("all"))
        st = await stats.summary(s, make_period("all"))
    assert (v.new_users, v.activated, v.openers, v.acts_ref, v.invited_users, v.invited_activated) == (3, 2, 1, 1, 2, 1)
    assert v.k_total == 0.5 and v.k_ref == 0.5 and v.k_comp == 0
    assert v.invites_per_opener == 2.0  # у единственного открывшего «Пригласить» два реферальных старта
    assert (st.new, st.activated) == (3, 2)

    text = sent_texts(await tg.send(ADMIN, "/stats"))[0]
    assert "Сводка — Сегодня" in text and "Новые: <b>4</b>" in text  # 3 + сам админ, отправивший /stats
    markup = [b.callback_data for row in (await _last_markup(tg)) for b in row]
    assert "st:funnel:today" in markup and "st:summary:30d" in markup and "st:sources:today" in markup


async def _last_markup(tg):
    return [c for c in tg.fake.calls if getattr(c, "reply_markup", None)][-1].reply_markup.inline_keyboard


async def test_stats_period_filter(tg, db):
    old = datetime.now(timezone.utc) - timedelta(days=30)
    async with db() as s:
        s.add(User(tg_id=1, ref_code="old00001", created_at=old, activated_at=old, is_blocked=False))
        await s.commit()
        week = await stats.summary(s, make_period("7d"))
        total = await stats.summary(s, make_period("all"))
    assert (week.new, week.activated) == (0, 0)
    assert (total.new, total.activated) == (1, 1)


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
    tg.fake.members[(-1001, B)] = "member"
    await tg.click(B, "sphere:money")
    async with db() as s:
        names = set(await s.scalars(select(Event.name)))
    expected = {"start", "birth_date_set", "teaser_shown", "channels_prompt", "full_clicked", "channels_ok", "full_shown",
                "invite_opened", "share_clicked", "compat_link_created", "referral_started",
                "referral_activated", "unlock", "sphere_opened"}
    assert expected <= names, expected - names
