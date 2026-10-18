"""Блок 2: учёт активности, источники, воронка, удержание, каналы, антифрод, карточка, экспорт, дайджест, алерты."""
import csv
import io
from datetime import date, datetime, timedelta, timezone

from aiogram.methods import SendDocument
from sqlalchemy import select

from bot.db.models import Channel, DailyActivity, ErrorLog, Event, Referral, User
from bot.services import alerts, digest, stats
from bot.services.clock import day_start_utc, msk_today
from bot.services.stats import Period, make_period, retention_for, sparkline
from bot.services.users import source_from_args
from tests.conftest import sent_texts

ADMIN = 999
UTC = timezone.utc


async def _user(db, tg_id) -> User:
    async with db() as s:
        return await s.scalar(select(User).where(User.tg_id == tg_id))


def mk_user(tg_id, **kw) -> User:
    kw.setdefault("ref_code", f"code{tg_id:05d}")
    kw.setdefault("is_blocked", False)
    kw.setdefault("created_at", datetime.now(UTC))
    return User(tg_id=tg_id, **kw)


# --- Время по Москве ---

def test_today_is_moscow_not_utc():
    now = datetime(2026, 10, 18, 22, 30, tzinfo=UTC)  # в Москве уже 19-е, 01:30
    assert msk_today(now) == date(2026, 10, 19)
    today, yesterday = make_period("today", now), make_period("yesterday", now)
    assert today.since == datetime(2026, 10, 18, 21, 0, tzinfo=UTC)
    assert yesterday.since == datetime(2026, 10, 17, 21, 0, tzinfo=UTC) and yesterday.until == today.since
    assert msk_today(datetime(2026, 10, 18, 20, 59, tzinfo=UTC)) == date(2026, 10, 18)


def test_sparkline():
    assert sparkline([0, 0, 0]) == "▁▁▁"
    line = sparkline([0, 1, 2, 4, 8])
    assert len(line) == 5 and line[0] == "▁" and line[-1] == "█"


# --- Активность ---

async def test_last_seen_and_daily_activity_once_per_day(tg, db):
    await tg.send(5001, "/start")
    await tg.send(5001, "/help")
    await tg.click(5001, "invite")
    user = await _user(db, 5001)
    assert user.last_seen_at is not None
    async with db() as s:
        rows = list(await s.scalars(select(DailyActivity).where(DailyActivity.user_id == user.id)))
    assert [r.day for r in rows] == [msk_today()]


# --- Источники ---

def test_source_from_args():
    assert source_from_args("") == "organic"
    assert source_from_args("r_abc123") == "ref" and source_from_args("c_abc123") == "ref"
    assert source_from_args("s_vk_may") == "vk_may"
    assert source_from_args("s_" + "a" * 20) == "a" * 20
    assert source_from_args("s_" + "a" * 21) == "organic"  # длиннее 20
    assert source_from_args("s_bad-tag") == "organic" and source_from_args("s_") == "organic"
    assert source_from_args("whatever") == "organic"


async def test_source_saved_on_first_start_only(tg, db):
    await tg.send(5011, "/start s_vk_may")
    await tg.send(5011, "/start s_other")
    assert (await _user(db, 5011)).source == "vk_may"
    await tg.send(5012, "/start")
    assert (await _user(db, 5012)).source == "organic"
    code = (await _user(db, 5011)).ref_code
    await tg.send(5013, f"/start r_{code}")
    assert (await _user(db, 5013)).source == "ref"
    await tg.send(5014, f"/start c_{code}")
    assert (await _user(db, 5014)).source == "ref"


async def test_link_command(tg):
    text = sent_texts(await tg.send(ADMIN, "/link vk_may"))[0]
    assert "https://t.me/test_bot?start=s_vk_may" in text
    assert "Использование" in sent_texts(await tg.send(ADMIN, "/link плохой-тег"))[0]
    assert "Использование" in sent_texts(await tg.send(ADMIN, "/link " + "a" * 21))[0]


async def test_admin_commands_are_admin_only(tg):
    for cmd in ("/stats", "/sources", "/top_inviters", "/export", "/user 1", "/link x", "/digest"):
        assert await tg.send(5099, cmd) == []


# --- Воронка ---

async def test_funnel_steps_and_percentages(tg, db):
    A, B, C = 5101, 5102, 5103
    for u in (A, B, C):
        await tg.send(u, "/start")
    await tg.send(A, "07.03.1994")
    await tg.send(B, "23.11.1985")
    await tg.click(A, "full")
    await tg.click(A, "invite")
    await tg.click(A, "share:r")
    async with db() as s:
        steps = {st.title: st for st in await stats.funnel(s, make_period("all"))}
    assert steps["Нажали /start"].count == 3
    assert steps["Ввели дату"].count == 2 and steps["Ввели дату"].of_prev == "67%" and steps["Ввели дату"].of_start == "67%"
    assert steps["Увидели тизер"].count == 2
    assert steps["Нажали «Полный разбор»"].count == 1 and steps["Нажали «Полный разбор»"].of_prev == "50%"
    assert steps["Увидели разбор"].count == 1
    assert steps["Открыли «Пригласить»"].count == 1 and steps["Нажали «Поделиться»"].count == 1
    assert steps["Реферальный старт"].count == 0


# --- Удержание ---

def test_retention_for_ignores_unmatured_days():
    today = date(2026, 10, 18)
    users = [(1, date(2026, 10, 10)), (2, date(2026, 10, 10)), (3, date(2026, 10, 17))]
    active = {(1, date(2026, 10, 11)), (1, date(2026, 10, 17)), (3, date(2026, 10, 18))}
    r = retention_for(users, active, today)
    assert abs(r[1] - 2 / 3) < 1e-9  # D1: вернулись двое из трёх
    assert r[7] == 0.5  # D7: из двух созревших вернулся один (день 17-го)
    assert r[30] is None  # 30 дней ещё не прошло


async def test_retention_table_from_daily_activity(tg, db):
    now = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    d10 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    async with db() as s:
        users = [mk_user(1, created_at=d10), mk_user(2, created_at=d10), mk_user(3, created_at=d10)]
        s.add_all(users)
        await s.flush()
        s.add_all([DailyActivity(user_id=users[0].id, day=date(2026, 10, 11)),
                   DailyActivity(user_id=users[0].id, day=date(2026, 10, 17)),
                   DailyActivity(user_id=users[1].id, day=date(2026, 10, 11))])
        await s.commit()
        table, total = await stats.retention_table(s, 14, now)
    row = next(r for r in table if r.day == date(2026, 10, 10))
    assert row.size == 3 and round(row.d1, 2) == 0.67 and round(row.d7, 2) == 0.33 and row.d30 is None
    assert len(table) == 14 and total[30] is None


# --- Каналы ---

async def test_channel_stats_shown_passed_stuck(tg, db):
    now = datetime.now(UTC)
    async with db() as s:
        ch = Channel(chat_id=-1001, title="К", url="https://t.me/k", is_active=True, sort_order=1, joined_count=0)
        s.add(ch)
        passed, stuck, fresh = mk_user(1), mk_user(2), mk_user(3)
        s.add_all([passed, stuck, fresh])
        await s.flush()
        passed.channels_ok_at = now
        old = now - timedelta(hours=30)
        s.add_all([
            Event(user_id=passed.id, name="channels_prompt", payload={"missing": [ch.id]}, created_at=old),
            Event(user_id=passed.id, name="channels_ok", payload={"channels": [ch.id]}, created_at=now),
            Event(user_id=stuck.id, name="channels_prompt", payload={"missing": [ch.id]}, created_at=old),
            Event(user_id=fresh.id, name="channels_prompt", payload={"missing": [ch.id]}, created_at=now),
        ])
        await s.commit()
        row = (await stats.channel_stats(s, make_period("all"), now))[0]
    assert (row.shown, row.passed, row.stuck, row.conversion) == (3, 1, 1, "33%")


# --- Вирусность, антифрод ---

async def test_antifraud_heavy_inviters_and_uncounted(tg, db):
    now = datetime.now(UTC)
    async with db() as s:
        inviter = mk_user(1, username="boss")
        s.add(inviter)
        await s.flush()
        for i in range(12):
            friend = mk_user(100 + i, referrer_id=inviter.id, activated_at=now)
            s.add(friend)
            await s.flush()
            s.add(Referral(inviter_id=inviter.id, invited_id=friend.id, kind="ref", activated_at=now, counted=i < 10))
        await s.commit()
        v = await stats.virality(s, make_period("all"))
    assert v.uncounted == 2
    assert [(uid, n) for uid, _, n in v.heavy_inviters] == [(inviter.id, 12)]
    assert v.no_username == 12  # друзья без username
    assert v.acts_ref == 12


async def test_median_time_to_first_friend(tg, db):
    t0 = datetime.now(UTC) - timedelta(days=3)
    async with db() as s:
        inviter = mk_user(1, activated_at=t0)
        s.add(inviter)
        await s.flush()
        friend = mk_user(2, activated_at=t0 + timedelta(hours=5), referrer_id=inviter.id)
        s.add(friend)
        await s.flush()
        s.add(Referral(inviter_id=inviter.id, invited_id=friend.id, kind="ref", counted=True,
                       activated_at=t0 + timedelta(hours=5)))
        await s.commit()
        v = await stats.virality(s, make_period("all"))
    assert round(v.median_first_friend / 3600) == 5


async def test_top_inviters_command(tg, db):
    now = datetime.now(UTC)
    async with db() as s:
        top = mk_user(1, first_name="Лена", username="lena")
        s.add(top)
        await s.flush()
        for i in range(3):
            f = mk_user(10 + i)
            s.add(f)
            await s.flush()
            s.add(Referral(inviter_id=top.id, invited_id=f.id, kind="ref", activated_at=now, counted=i < 2))
        await s.commit()
    text = sent_texts(await tg.send(ADMIN, "/top_inviters"))[0]
    assert "Лена @lena" in text and "<b>2</b> / 1" in text


# --- Источники ---

async def test_sources_report(tg, db):
    now = datetime.now(UTC)
    async with db() as s:
        a1 = mk_user(1, source="vk", activated_at=now, channels_ok_at=now)
        a2 = mk_user(2, source="vk")
        b1 = mk_user(3, source="organic", activated_at=now)
        s.add_all([a1, a2, b1])
        await s.flush()
        friend = mk_user(4, source="ref", referrer_id=a1.id, activated_at=now)
        s.add(friend)
        await s.flush()
        s.add(Referral(inviter_id=a1.id, invited_id=friend.id, kind="ref", activated_at=now, counted=True))
        await s.commit()
        rows = {r.source: r for r in await stats.source_stats(s, make_period("all"))}
    vk = rows["vk"]
    assert (vk.users, vk.activated, vk.channels_ok, vk.ref_activations) == (2, 1, 1, 1) and vk.k == 1.0
    assert rows["organic"].users == 1 and rows["ref"].users == 1
    text = sent_texts(await tg.send(ADMIN, "/sources"))[0]
    assert "<b>vk</b>: 2 · 50% · 50% · 1.00" in text


# --- Контент ---

async def test_content_decades_and_arcana_without_exact_dates(tg, db):
    await tg.send(5201, "/start")
    await tg.send(5201, "07.03.1994")
    await tg.send(5202, "/start")
    await tg.send(5202, "23.11.1985")
    async with db() as s:
        c = await stats.content(s, make_period("all"))
    assert c.decades == {1990: 1, 1980: 1}
    assert dict(c.top_personality) == {7: 1, 5: 1}  # 07 → Колесница; 23 → 5
    from bot.handlers.admin_stats import render_content
    async with db() as s:
        rendered = await render_content(s, make_period("all"))
    assert "1994" not in rendered and "07.03" not in rendered and "1990-е" in rendered


# --- Карточка пользователя и экспорт ---

async def test_user_card_has_no_exact_birth_date(tg, db):
    await tg.send(5301, "/start s_vk_may", first_name="Мария")
    await tg.send(5301, "07.03.1994", first_name="Мария")
    text = sent_texts(await tg.send(ADMIN, "/user 5301"))[0]
    assert "5301" in text and "1994" in text and "Колесница" in text and "vk_may" in text
    assert "07.03.1994" not in text and "1994-03-07" not in text
    assert "Мария" in text
    assert "не найден" in sent_texts(await tg.send(ADMIN, "/user 424242"))[0]
    async with db() as s:
        u = await s.scalar(select(User).where(User.tg_id == 5301))
        u.username = "maria_x"
        await s.commit()
    assert "5301" in sent_texts(await tg.send(ADMIN, "/user @Maria_X"))[0]


async def test_export_csv_has_no_birth_dates_or_names(tg, db):
    await tg.send(5401, "/start s_vk_may", first_name="Секретное")
    await tg.send(5401, "07.03.1994", first_name="Секретное")
    code = (await _user(db, 5401)).ref_code
    await tg.send(5402, f"/start r_{code}")
    await tg.send(5402, "23.11.1985")
    calls = await tg.send(ADMIN, "/export")
    doc = next(c for c in tg.fake.calls if isinstance(c, SendDocument))
    raw = doc.document.data if hasattr(doc.document, "data") else doc.document.read()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    assert list(rows[0]) == ["tg_id", "source", "gender", "created_at", "activated_at", "channels_ok_at", "referrer_id",
                             "counted_refs", "last_seen_at", "is_blocked"]
    by_tg = {r["tg_id"]: r for r in rows}
    assert by_tg["5401"]["source"] == "vk_may" and by_tg["5401"]["counted_refs"] == "1"
    assert by_tg["5402"]["referrer_id"] == "5401"
    text = raw.decode("utf-8-sig")
    assert "1994" not in text and "Секретное" not in text and "07.03" not in text


# --- Дайджест и алерты ---

async def test_digest_toggle_and_send_once_per_day(tg, db):
    # по умолчанию дайджест включён, первое /digest его выключает
    assert "выключен" in sent_texts(await tg.send(ADMIN, "/digest"))[0]
    async with db() as s:
        assert not await digest.is_enabled(s, ADMIN)
    assert "включён" in sent_texts(await tg.send(ADMIN, "/digest"))[0]
    async with db() as s:
        assert await digest.is_enabled(s, ADMIN)

    now = datetime(2026, 10, 18, 6, 0, tzinfo=UTC)  # 09:00 МСК
    assert await digest.send_digest(tg.bot, db, now) is True
    assert await digest.send_digest(tg.bot, db, now) is False  # второй раз за день не шлём
    text = [t for t in sent_texts(tg.fake.calls, ADMIN) if "Дайджест" in t][-1]
    assert "Дайджест за 17.10" in text and "D1" in text and "K:" in text


async def test_digest_not_sent_when_disabled(tg, db):
    await tg.send(ADMIN, "/digest")  # выключили
    before = len([t for t in sent_texts(tg.fake.calls, ADMIN) if "Дайджест" in t])
    await digest.send_digest(tg.bot, db, datetime(2026, 10, 19, 6, 0, tzinfo=UTC))
    assert len([t for t in sent_texts(tg.fake.calls, ADMIN) if "Дайджест" in t]) == before


async def test_alert_on_many_errors_throttled_hourly(tg, db):
    now = datetime.now(UTC)
    async with db() as s:
        s.add_all([ErrorLog(kind="X", message="boom", created_at=now) for _ in range(21)])
        await s.commit()
    assert await alerts.run_alerts(tg.bot, db, now) == 1
    assert any("ошибок: 21" in t for t in sent_texts(tg.fake.calls, ADMIN))
    assert await alerts.run_alerts(tg.bot, db, now + timedelta(minutes=30)) == 0  # не чаще раза в час
    later = now + timedelta(minutes=61)
    async with db() as s:
        s.add_all([ErrorLog(kind="X", message="boom", created_at=later) for _ in range(21)])
        await s.commit()
    assert await alerts.run_alerts(tg.bot, db, later) == 1


async def test_alert_not_admin_in_channel(tg, db):
    async with db() as s:
        s.add(Channel(chat_id=-1007, title="Потеряли", url="https://t.me/x", is_active=True, sort_order=1, joined_count=0))
        await s.commit()
    # FakeTelegram: бот в канале не админ
    assert await alerts.run_alerts(tg.bot, db) == 1
    assert any("больше не администратор" in t and "Потеряли" in t for t in sent_texts(tg.fake.calls, ADMIN))


async def test_alert_k_drop_only_with_enough_activations(tg, db):
    now = datetime.now(UTC)
    async with db() as s:
        # неделя назад: много реферальных активаций (K = 1.0), за последние сутки — 25 активаций без рефералов
        old = now - timedelta(days=3)
        inviter = mk_user(1, activated_at=old)
        s.add(inviter)
        await s.flush()
        for i in range(30):
            f = mk_user(1000 + i, activated_at=old, referrer_id=inviter.id)
            s.add(f)
            await s.flush()
            s.add(Referral(inviter_id=inviter.id, invited_id=f.id, kind="ref", counted=True, activated_at=old))
        for i in range(25):
            s.add(mk_user(2000 + i, activated_at=now - timedelta(hours=2)))
        await s.commit()
        found = dict(await alerts.collect_alerts(tg.bot, s, now))
    assert "k_drop" in found


async def test_no_k_alert_when_few_activations(tg, db):
    now = datetime.now(UTC)
    async with db() as s:
        s.add_all([mk_user(3000 + i, activated_at=now) for i in range(5)])
        await s.commit()
        assert "k_drop" not in dict(await alerts.collect_alerts(tg.bot, s, now))


async def test_record_error_writes_to_errors_table(tg, db):
    from bot.main import record_error
    await record_error(db, ValueError("плохо"))
    async with db() as s:
        row = await s.scalar(select(ErrorLog))
        assert (row.kind, row.message) == ("ValueError", "плохо")
        assert sum(n for _, n in await stats.error_counts(s, 7)) == 1


async def test_mailings_report_counts_push_events(tg, db):
    async with db() as s:
        u = mk_user(1, notify_daily=True)
        s.add(u)
        await s.flush()
        s.add_all([Event(user_id=u.id, name="push_sent", payload={"kind": "daily"}),
                   Event(user_id=u.id, name="push_sent", payload={"kind": "daily"}),
                   Event(user_id=u.id, name="push_blocked", payload={"kind": "daily"}),
                   Event(user_id=u.id, name="push_clicked", payload={"kind": "daily"})])
        await s.commit()
        m = await stats.mailings(s, make_period("all"))
    assert m.daily_subscribers == 1
    assert [(r.kind, r.sent, r.blocked, r.clicked) for r in m.pushes] == [("daily", 2, 1, 1)]


async def test_every_section_and_period_renders(tg, db):
    """Все разделы /stats на всех периодах открываются без ошибок — и на пустой базе, и с данными."""
    from bot.handlers.admin_stats import RENDERERS
    from bot.services.stats import PERIOD_KEYS

    async def open_all():
        for section in RENDERERS:
            for period in PERIOD_KEYS:
                await tg.click(ADMIN, f"st:{section}:{period}")

    await open_all()  # пустая база (админ сам создастся как пользователь)
    A, B = 5501, 5502
    await tg.send(A, "/start s_vk")
    await tg.send(A, "07.03.1994")
    await tg.click(A, "full")
    await tg.click(A, "invite")
    code = (await _user(db, A)).ref_code
    await tg.send(B, f"/start r_{code}")
    await tg.send(B, "23.11.1985")
    await tg.click(B, "compat")
    await open_all()
    async with db() as s:
        for section, render in RENDERERS.items():
            text = await render(s, make_period("all"))
            assert text and len(text) < 4096, section
