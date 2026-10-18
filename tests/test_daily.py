"""Блок 3: число дня — формула, тексты, подписка, рассылка, лимиты Telegram."""
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from bot.content import messages as msg
from bot.db.models import Event, PushLog, Unlock, User
from bot.services import daily, pushes, texts
from bot.services.gender import has_templates, render
from bot.services.matrix_calc import calculate, personal_day, reduce
from bot.services.pushes import RateLimiter
from tests.conftest import alerts, buttons, sent, sent_texts

U, ADMIN = 6001, 999
UTC = timezone.utc
NOW_9_MSK = datetime(2026, 10, 18, 6, 0, tzinfo=UTC)  # 09:00 по Москве


async def _user(db, tg_id) -> User:
    async with db() as s:
        return await s.scalar(select(User).where(User.tg_id == tg_id))


async def _register(tg, tg_id, birth="07.03.1994"):
    await tg.send(tg_id, "/start")
    await tg.send(tg_id, birth)


async def _subscribe(db, tg_id, hour=9, **kw):
    async with db() as s:
        u = await s.scalar(select(User).where(User.tg_id == tg_id))
        u.notify_daily, u.notify_hour = True, hour
        for k, v in kw.items():
            setattr(u, k, v)
        await s.commit()


# --- Формула и тексты ---

def test_personal_day_formula():
    m = calculate(date(1994, 3, 7))  # аркан личности — 7
    assert personal_day(m, date(2026, 10, 18)) == reduce(7 + 18 + 10) == 8
    assert personal_day(m, date(2026, 1, 1)) == 9
    assert personal_day(m, date(2026, 12, 31)) == reduce(7 + 31 + 12)


def test_personal_day_always_valid_arcanum():
    for a_day in range(1, 29):
        m = calculate(date(1990, 1, a_day))
        for offset in range(0, 366, 7):
            n = personal_day(m, date(2026, 1, 1) + timedelta(days=offset))
            assert 1 <= n <= 22


def test_day_texts_exist_and_are_short():
    for n in range(1, 23):
        for gender in ("f", "m"):
            t = render(texts.text(n, "day"), gender)
            assert 150 <= len(t) <= 350 and not has_templates(t), (n, len(t))
            assert 2 <= len(re.findall(r"[.!?]", t)) <= 4 and "Совет:" in t, n


# --- Подписка ---

async def test_offer_after_full_and_enable_flow(tg, db):
    await _register(tg, U)
    calls = await tg.click(U, "full")
    offer = sent(calls)[-1]
    assert offer.text == msg.DAILY_OFFER
    assert [b.callback_data for b in buttons(offer)] == ["daily:on", "daily:no"]
    assert [b.text for b in buttons(offer)] == [msg.BTN_DAILY_YES, msg.BTN_DAILY_NO]
    # повторный просмотр разбора не повторяет предложение
    assert msg.DAILY_OFFER not in sent_texts(await tg.click(U, "full"))

    await tg.click(U, "daily:on")
    assert [c for c in tg.fake.calls if getattr(c, "text", "") == msg.DAILY_PICK_HOUR]
    picker = [c for c in tg.fake.calls if getattr(c, "text", "") == msg.DAILY_PICK_HOUR][-1]
    assert [b.text for b in picker.reply_markup.inline_keyboard[0]] == ["7:00", "8:00", "9:00", "10:00", "12:00"]
    await tg.click(U, "daily:h:12")
    user = await _user(db, U)
    assert user.notify_daily is True and user.notify_hour == 12


async def test_decline_offer_and_invalid_hour(tg, db):
    await _register(tg, U)
    await tg.click(U, "full")
    await tg.click(U, "daily:no")
    await tg.click(U, "daily:h:3")  # такого времени нет в выборе
    user = await _user(db, U)
    assert user.notify_daily is False
    async with db() as s:
        names = set(await s.scalars(select(Event.name).where(Event.user_id == user.id)))
    assert {"daily_offer_shown", "daily_declined"} <= names


async def test_settings_toggle_daily_time_and_marketing(tg, db):
    await _register(tg, U)
    calls = await tg.send(U, "/settings")
    text = sent_texts(calls)[0]
    assert "Число дня: <b>выключено</b>" in text and "Новости и напоминания: <b>включены</b>" in text
    assert "daily:on" in [b.callback_data for b in buttons(sent(calls)[0])]
    assert "mkt:off" in [b.callback_data for b in buttons(sent(calls)[0])]

    await tg.click(U, "daily:on")
    await tg.click(U, "daily:h:8")
    await tg.click(U, "mkt:off")
    user = await _user(db, U)
    assert (user.notify_daily, user.notify_hour, user.notify_marketing) == (True, 8, False)
    calls = await tg.send(U, "/settings")
    assert "включено, 8:00 МСК" in sent_texts(calls)[0] and "выключены" in sent_texts(calls)[0]
    assert {"daily:off", "daily:time", "mkt:on"} <= {b.callback_data for b in buttons(sent(calls)[0])}

    await tg.click(U, "daily:off")
    await tg.click(U, "mkt:on")
    user = await _user(db, U)
    assert (user.notify_daily, user.notify_marketing) == (False, True)


async def test_stop_disables_marketing_only(tg, db):
    await _register(tg, U)
    await _subscribe(db, U)
    assert sent_texts(await tg.send(U, "/stop")) == [msg.STOP_DONE]
    user = await _user(db, U)
    assert user.notify_marketing is False and user.notify_daily is True


# --- Рассылка ---

async def test_daily_push_sent_once_at_chosen_hour(tg, db):
    await _register(tg, U)
    await _register(tg, U + 1, "23.11.1985")
    await _subscribe(db, U, 9)
    await _subscribe(db, U + 1, 10)
    start = len(tg.fake.calls)
    assert await daily.run_daily(tg.bot, db, NOW_9_MSK) == 1
    pushed = tg.fake.calls[start:]
    assert [m.chat_id for m in sent(pushed)] == [U]  # в 9:00 — только тот, кто выбрал 9:00
    text = sent(pushed)[0].text
    assert text.startswith("☀️ <b>Число дня — 8. ") and "Совет:" in text
    assert [b.callback_data for b in buttons(sent(pushed)[0])] == ["pc:daily:tomorrow", "pc:daily:invite"]
    assert [b.text for b in buttons(sent(pushed)[0])] == [msg.BTN_TOMORROW_LOCKED, msg.BTN_INVITE_FRIEND]

    # второй запуск в тот же день никому не повторяет
    assert await daily.run_daily(tg.bot, db, NOW_9_MSK + timedelta(minutes=1)) == 0
    async with db() as s:
        kinds = list(await s.scalars(select(PushLog.kind)))
    assert kinds == ["daily:2026-10-18"]
    # следующий день — новый пуш
    assert await daily.run_daily(tg.bot, db, NOW_9_MSK + timedelta(days=1)) == 1


async def test_daily_skips_blocked_unsubscribed_and_no_date(tg, db):
    await _register(tg, U)
    await _register(tg, U + 1)
    await _register(tg, U + 2)
    await tg.send(U + 3, "/start")  # без даты рождения
    for uid in (U, U + 1, U + 3):
        await _subscribe(db, uid)
    async with db() as s:
        (await s.scalar(select(User).where(User.tg_id == U + 1))).is_blocked = True
        await s.commit()
    start = len(tg.fake.calls)
    await daily.run_daily(tg.bot, db, NOW_9_MSK)
    assert [m.chat_id for m in sent(tg.fake.calls[start:])] == [U]


async def test_daily_catches_up_after_downtime_but_not_too_late(tg, db):
    await _register(tg, U)
    await _subscribe(db, U, 9)
    assert await daily.run_daily(tg.bot, db, NOW_9_MSK.replace(hour=14)) == 0  # 17:00 МСК — поздно
    assert await daily.run_daily(tg.bot, db, NOW_9_MSK.replace(hour=8)) == 1  # 11:00 МСК — ещё можно


async def test_daily_forbidden_marks_blocked(tg, db):
    await _register(tg, U)
    await _subscribe(db, U)
    tg.fake.forbidden.add(U)
    assert await daily.run_daily(tg.bot, db, NOW_9_MSK) == 0
    assert (await _user(db, U)).is_blocked is True
    async with db() as s:
        names = list(await s.scalars(select(Event.name).where(Event.name.in_(["push_sent", "push_blocked"]))))
    assert names == ["push_blocked"]
    # заблокировавшему больше не пишем
    tg.fake.forbidden.discard(U)
    assert await daily.run_daily(tg.bot, db, NOW_9_MSK + timedelta(minutes=1)) == 0


async def test_daily_retry_after_waits_and_retries(tg, db, monkeypatch):
    await _register(tg, U)
    await _subscribe(db, U)
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(pushes, "_sleep", fake_sleep)
    tg.fake.retry_once[U] = 2
    assert await daily.run_daily(tg.bot, db, NOW_9_MSK) == 1
    assert slept == [3]  # retry_after + 1
    async with db() as s:
        assert len(list(await s.scalars(select(Event).where(Event.name == "push_sent")))) == 1  # доставлено один раз
        assert len(list(await s.scalars(select(PushLog)))) == 1


async def test_daily_uses_gender_forms_for_each_recipient(tg, db):
    """Число дня 12 содержит «{зашёл|зашла}» — получатель видит свою форму."""
    m = calculate(date(1994, 3, 7))
    day12 = next(date(2026, 1, 1) + timedelta(days=i) for i in range(400)
                 if personal_day(m, date(2026, 1, 1) + timedelta(days=i)) == 12)
    now = datetime.combine(day12, datetime.min.time(), tzinfo=UTC) + timedelta(hours=6)
    for uid, gender, form in ((U, "f", "зашла"), (U + 1, "m", "зашёл")):
        tg.auto_gender = gender
        await _register(tg, uid)
        await _subscribe(db, uid)
    start = len(tg.fake.calls)
    await daily.run_daily(tg.bot, db, now)
    by_chat = {m_.chat_id: m_.text for m_ in sent(tg.fake.calls[start:])}
    assert "зашла" in by_chat[U] and "зашёл" not in by_chat[U]
    assert "зашёл" in by_chat[U + 1] and "зашла" not in by_chat[U + 1]
    assert not has_templates(by_chat[U]) and not has_templates(by_chat[U + 1])


# --- Кнопки пуша ---

async def test_push_button_marks_click_once_and_runs_action(tg, db):
    await _register(tg, U)
    await _subscribe(db, U)
    await daily.run_daily(tg.bot, db, NOW_9_MSK)
    calls = await tg.click(U, "pc:daily:invite")
    assert "Приглашай друзей" in sent_texts(calls)[0]
    await tg.click(U, "pc:daily:invite")
    async with db() as s:
        row = await s.scalar(select(PushLog))
        clicks = list(await s.scalars(select(Event).where(Event.name == "push_clicked")))
    assert row.clicked_at is not None and len(clicks) == 1 and clicks[0].payload == {"kind": "daily"}


async def test_tomorrow_forecast_locked_then_opened_by_unlock(tg, db):
    await _register(tg, U)
    calls = await tg.click(U, "pc:daily:tomorrow")
    assert alerts(calls) == [msg.TOMORROW_LOCKED_ALERT]
    from bot.services.clock import msk_today
    user = await _user(db, U)
    async with db() as s:
        s.add(Unlock(user_id=user.id, item=daily.tomorrow_item(msk_today()), source="quest"))
        await s.commit()
    calls = await tg.click(U, "pc:daily:tomorrow")
    assert alerts(calls) == [] and "Прогноз на завтра" in sent_texts(calls)[0]


# --- Лимит 20 сообщений в секунду ---

async def test_rate_limiter_never_exceeds_20_per_second():
    clock = {"t": 0.0}
    slept = []

    async def sleep(s):
        slept.append(s)
        clock["t"] += s

    limiter = RateLimiter(20, clock=lambda: clock["t"], sleep=sleep)
    moments = []
    for _ in range(101):
        await limiter.acquire()
        moments.append(clock["t"])
    # в любом окне в 1 секунду — не больше 20 отправок
    for i, t in enumerate(moments):
        assert sum(1 for x in moments[i:] if x < t + 1.0 - 1e-9) <= 20
    assert moments[-1] >= 100 / 20 - 1e-9
