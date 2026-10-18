"""Блок 5: ежедневный квест «пригласи друга — откроем прогноз на завтра»."""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from bot.content import messages as msg
from bot.db.models import Event, Quest, Referral, Unlock, User
from bot.handlers.admin_stats import render_mailings
from bot.services import daily, quests, stats
from bot.services.clock import msk_today
from bot.services.matrix_calc import calculate, personal_day
from bot.services.stats import make_period
from tests.conftest import buttons, sent, sent_texts

A, B, C = 8001, 8002, 8003
UTC = timezone.utc


async def _user(db, tg_id) -> User:
    async with db() as s:
        return await s.scalar(select(User).where(User.tg_id == tg_id))


async def _register(tg, tg_id, birth="07.03.1994", start="/start"):
    await tg.send(tg_id, start)
    return await tg.send(tg_id, birth)


async def _quests(db):
    async with db() as s:
        return list(await s.scalars(select(Quest)))


async def test_quest_shown_in_invite_once_per_day(tg, db):
    await _register(tg, A)
    text = sent_texts(await tg.send(A, "/invite"))[0]
    assert "🎯 <b>Квест дня:</b> пригласи 1 друга до 23:59 — откроем прогноз на завтра. Выполнено: 0/1." in text
    await tg.send(A, "/invite")
    [q] = await _quests(db)
    assert (q.day, q.goal, q.progress, q.completed_at, q.rewarded) == (msk_today(), 1, 0, None, False)
    async with db() as s:
        shown = list(await s.scalars(select(Event).where(Event.name == "quest_shown")))
    assert len(shown) == 1


async def test_no_quest_without_birth_date(tg, db):
    await tg.send(A, "/start")
    assert "Квест дня" not in sent_texts(await tg.send(A, "/invite"))[0]
    assert await _quests(db) == []


async def test_friend_completes_quest_and_opens_tomorrow(tg, db):
    await _register(tg, A)
    await tg.send(A, "/invite")
    code = (await _user(db, A)).ref_code
    await tg.send(B, f"/start r_{code}")
    calls = await tg.send(B, "23.11.1985")

    push = sent(calls, A)[0]
    assert "Квест выполнен!" in push.text and "Прогноз на завтра открыт" in push.text
    assert "pc:friend:tomorrow" in [b.callback_data for b in buttons(push)]
    [q] = await _quests(db)
    assert q.progress == 1 and q.completed_at is not None and q.rewarded is True
    async with db() as s:
        unlock = await s.scalar(select(Unlock).where(Unlock.item == daily.tomorrow_item(msk_today())))
        done = list(await s.scalars(select(Event).where(Event.name == "quest_done")))
    assert unlock.source == "quest" and len(done) == 1

    # кнопка открывает текст на завтра: число дня считается для завтрашней даты
    calls = await tg.click(A, "pc:friend:tomorrow")
    tomorrow = msk_today() + timedelta(days=1)
    n = personal_day(calculate(date(1994, 3, 7)), tomorrow)
    text = sent_texts(calls)[0]
    assert f"Прогноз на завтра, {tomorrow:%d.%m} — {n}." in text

    # /invite теперь показывает выполненный квест
    assert msg.QUEST_DONE in sent_texts(await tg.send(A, "/invite"))[0]


async def test_second_friend_does_not_reward_twice(tg, db):
    await _register(tg, A)
    await tg.send(A, "/invite")
    code = (await _user(db, A)).ref_code
    for friend, birth in ((B, "23.11.1985"), (C, "01.01.2001")):
        await tg.send(friend, f"/start r_{code}")
        calls = await tg.send(friend, birth)
    assert "Квест выполнен" not in sent(calls, A)[0].text  # второй друг: квест уже закрыт
    async with db() as s:
        assert len(list(await s.scalars(select(Unlock).where(Unlock.source == "quest")))) == 1
        assert len(list(await s.scalars(select(Event).where(Event.name == "quest_done")))) == 1


async def test_quest_not_progressed_if_not_shown(tg, db):
    await _register(tg, A)  # /invite не открывали — квеста нет
    code = (await _user(db, A)).ref_code
    await tg.send(B, f"/start r_{code}")
    calls = await tg.send(B, "23.11.1985")
    assert "Квест" not in sent(calls, A)[0].text
    assert await _quests(db) == []
    # а при первом показе уже активированный сегодня друг сразу закрывает квест
    text = sent_texts(await tg.send(A, "/invite"))[0]
    assert msg.QUEST_DONE in text


async def test_uncounted_friend_does_not_progress_quest(tg, db):
    async with db() as s:
        inviter = User(tg_id=A, ref_code="aaaa0001", is_blocked=False, birth_date=date(1994, 3, 7))
        friend = User(tg_id=B, ref_code="bbbb0001", is_blocked=False, activated_at=datetime.now(UTC))
        s.add_all([inviter, friend])
        await s.flush()
        s.add(Referral(inviter_id=inviter.id, invited_id=friend.id, kind="ref", counted=False,
                       activated_at=datetime.now(UTC)))
        await s.commit()
        quest = await quests.ensure(s, inviter)
    assert quest.progress == 0 and quest.completed_at is None


async def test_quest_resets_next_day(tg, db):
    await _register(tg, A)
    await tg.send(A, "/invite")
    code = (await _user(db, A)).ref_code
    await tg.send(B, f"/start r_{code}")
    await tg.send(B, "23.11.1985")
    tomorrow = msk_today() + timedelta(days=1)
    async with db() as s:
        user = await s.scalar(select(User).where(User.tg_id == A))
        new = await quests.ensure(s, user, tomorrow)
        await s.commit()
    assert new.progress == 0 and new.completed_at is None  # вчерашний друг не засчитывается
    assert len(await _quests(db)) == 2


async def test_quest_in_daily_message_and_tomorrow_button_state(tg, db):
    await _register(tg, A)
    async with db() as s:
        (await s.scalar(select(User).where(User.tg_id == A))).notify_daily = True
        await s.commit()
    now = datetime.combine(msk_today(), datetime.min.time(), tzinfo=UTC) + timedelta(hours=6)  # 09:00 МСК
    start = len(tg.fake.calls)
    await daily.run_daily(tg.bot, db, now)
    push = sent(tg.fake.calls[start:], A)[0]
    assert "Квест дня" in push.text and "0/1" in push.text
    assert buttons(push)[0].text == msg.BTN_TOMORROW_LOCKED

    code = (await _user(db, A)).ref_code
    await tg.send(B, f"/start r_{code}")
    await tg.send(B, "23.11.1985")
    async with db() as s:
        user = await s.scalar(select(User).where(User.tg_id == A))
        _, markup = await daily.build_push(s, user, msk_today())
    assert markup.inline_keyboard[0][0].text == msg.BTN_TOMORROW_OPEN


async def test_quest_metrics_in_mailings_report(tg, db):
    await _register(tg, A)
    await _register(tg, B)
    await tg.send(A, "/invite")
    await tg.send(B, "/invite")
    code = (await _user(db, A)).ref_code
    await tg.send(C, f"/start r_{code}")
    await tg.send(C, "23.11.1985")
    async with db() as s:
        m = await stats.mailings(s, make_period("all"))
        text = await render_mailings(s, make_period("all"))
    assert m.quests == {"shown": 2, "done": 1}
    assert "Квесты:</b> показано 2, выполнено 1 (50%)" in text
