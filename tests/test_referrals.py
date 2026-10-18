from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from bot.content import messages as msg
from bot.db.models import Event, Referral, Unlock, User
from bot.services.referrals import friends, rewards_due, unlock_plan
from tests.conftest import alerts, buttons, sent, sent_texts

# --- Чистая логика шкалы (пороги 1, 2, 3, 5, 7, 10) ---


def test_first_threshold_opens_money():
    assert rewards_due(1, set(), 0) == ["money"]


def test_gift_does_not_burn_threshold():
    # у приглашённого уже есть подарок «Деньги» — первый порог открывает следующее
    assert rewards_due(1, {"money"}, 0) == ["relationships"]


def test_several_thresholds_at_once():
    assert rewards_due(3, set(), 0) == ["money", "relationships", "karma"]


def test_no_new_rewards_between_thresholds():
    assert rewards_due(4, {"money", "relationships", "karma"}, 3) == []
    assert rewards_due(5, {"money", "relationships", "karma"}, 3) == ["compat_full_and_year"]


def test_all_rewards_with_gift():
    assert rewards_due(10, {"money"}, 0) == ["relationships", "karma", "compat_full_and_year", "practices", "ai"]


def test_unlock_plan():
    assert unlock_plan(0, set()) == {
        "money": 1, "relationships": 2, "karma": 3, "compat_full_and_year": 5, "practices": 7, "ai": 10,
    }
    assert unlock_plan(0, {"money"}) == {
        "relationships": 1, "karma": 2, "compat_full_and_year": 3, "practices": 5, "ai": 7,
    }
    assert unlock_plan(2, {"money", "relationships", "karma"}) == {
        "compat_full_and_year": 1, "practices": 3, "ai": 5,
    }
    assert unlock_plan(10, set(REWARD for REWARD in unlock_plan(0, set()))) == {}


@pytest.mark.parametrize("n, text", [(1, "1 друг"), (2, "2 друга"), (5, "5 друзей"), (11, "11 друзей"),
                                     (21, "21 друг"), (22, "22 друга"), (0, "0 друзей")])
def test_friends_plural(n, text):
    assert friends(n) == text


# --- Сквозные сценарии ---

A, B, C, D = 2001, 2002, 2003, 2004


async def _user(db, tg_id) -> User:
    async with db() as s:
        return await s.scalar(select(User).where(User.tg_id == tg_id))


async def _unlocks(db, tg_id) -> dict[str, str]:
    async with db() as s:
        u = await s.scalar(select(User).where(User.tg_id == tg_id))
        return {x.item: x.source for x in await s.scalars(select(Unlock).where(Unlock.user_id == u.id))}


async def _register(tg, tg_id, date="07.03.1994", start="/start", name="Аня"):
    await tg.send(tg_id, start, first_name=name)
    return await tg.send(tg_id, date, first_name=name)


async def test_referral_gift_activation_and_notification(tg, db):
    await _register(tg, A)
    code = (await _user(db, A)).ref_code

    calls = await tg.send(B, f"/start r_{code}", first_name="Борис")
    assert sent_texts(calls) == [msg.START_HELLO + msg.START_REF_GIFT, msg.GENDER_ASK, msg.ASK_DATE]
    assert await _unlocks(db, B) == {"money": "gift"}
    assert (await _user(db, B)).referrer_id == (await _user(db, A)).id

    calls = await tg.send(B, "23.11.1985", first_name="Борис")
    to_a = sent(calls, A)
    assert len(to_a) == 1
    assert to_a[0].text.startswith("🎉 По твоей ссылке рассчитали матрицу! Активировано: <b>1</b>")
    assert "💰 Деньги" in to_a[0].text
    assert buttons(to_a[0])[0].callback_data == "sphere:money"
    assert await _unlocks(db, A) == {"money": "referral"}

    # у B подарок открыт — сфера показывается
    calls = await tg.click(B, "sphere:money", first_name="Борис")
    assert "Твоя денежная энергия — 5." in sent_texts(calls)[0]

    # у A после 1 друга следующий — «Отношения» за 1 друга
    text = sent_texts(await tg.send(A, "/invite"))[0]
    assert "Активировано: 1.</b> До сферы «Отношения»: 1 друг." in text
    assert f"https://t.me/test_bot?start=r_{code}" in text


async def test_referrer_is_fixed_on_first_start_only(tg, db):
    await _register(tg, A)
    await _register(tg, B)
    a_code = (await _user(db, A)).ref_code
    b_code = (await _user(db, B)).ref_code

    await tg.send(C, f"/start r_{a_code}")
    await tg.send(C, f"/start r_{b_code}")
    assert (await _user(db, C)).referrer_id == (await _user(db, A)).id

    # существующий пользователь по чужой ссылке не привязывается и сразу видит свой тизер
    calls = await tg.send(B, f"/start r_{a_code}")
    assert (await _user(db, B)).referrer_id is None
    assert "Матрица Судьбы" in sent_texts(calls)[0]

    # повторный ввод даты не активирует второй раз
    await tg.send(C, "01.01.2001")
    await tg.send(C, "/settings")
    await tg.click(C, "change_date")
    calls = await tg.send(C, "02.02.2002")
    assert sent(calls, A) == []
    async with db() as s:
        assert len(list(await s.scalars(select(Referral)))) == 1


async def test_unknown_ref_code_is_ignored(tg, db):
    calls = await tg.send(B, "/start r_nonexist")
    assert sent_texts(calls) == [msg.START_HELLO, msg.GENDER_ASK, msg.ASK_DATE]
    assert (await _user(db, B)).referrer_id is None


async def test_antifraud_daily_limit(tg, db):
    await _register(tg, A)
    async with db() as s:
        a = await s.scalar(select(User).where(User.tg_id == A))
        now = datetime.now(timezone.utc)
        for i in range(20):
            u = User(tg_id=50000 + i, ref_code=f"fake{i:04d}", is_blocked=False)
            s.add(u)
            await s.flush()
            s.add(Referral(inviter_id=a.id, invited_id=u.id, kind="ref", counted=True,
                           activated_at=now - timedelta(hours=1)))
        await s.commit()

    code = (await _user(db, A)).ref_code
    await tg.send(B, f"/start r_{code}")
    calls = await tg.send(B, "07.03.1994")
    assert sent(calls, A) == []  # не засчитано — уведомления нет
    async with db() as s:
        b = await s.scalar(select(User).where(User.tg_id == B))
        ref = await s.scalar(select(Referral).where(Referral.invited_id == b.id))
        assert ref.activated_at is not None and ref.counted is False


async def test_blocked_inviter_marked(tg, db):
    await _register(tg, A)
    code = (await _user(db, A)).ref_code
    tg.fake.forbidden.add(A)
    await tg.send(B, f"/start r_{code}")
    await tg.send(B, "07.03.1994")
    assert (await _user(db, A)).is_blocked is True
    # пользователь вернулся — флаг снимается
    tg.fake.forbidden.clear()
    await tg.send(A, "/help")
    assert (await _user(db, A)).is_blocked is False


async def test_share_button_tracks_event(tg, db):
    await _register(tg, A)
    calls = await tg.click(A, "share:r")
    url = buttons(sent(calls)[0])[0].url
    assert url.startswith("https://t.me/share/url?url=https%3A%2F%2Ft.me%2Ftest_bot%3Fstart%3Dr_")
    async with db() as s:
        names = list(await s.scalars(select(Event.name)))
    assert "share_clicked" in names


async def test_rewards_restored_after_deletedata(tg, db):
    await _register(tg, A)
    code = (await _user(db, A)).ref_code
    await _register(tg, B, start=f"/start r_{code}")
    assert await _unlocks(db, A) == {"money": "referral"}

    await tg.send(A, "/deletedata")
    await tg.click(A, "delete_yes")
    assert await _unlocks(db, A) == {}
    await tg.send(A, "07.03.1994")
    assert await _unlocks(db, A) == {"money": "referral"}


async def test_three_friends_open_karma(tg, db):
    await _register(tg, A)
    code = (await _user(db, A)).ref_code
    for tg_id in (B, C, D):
        await _register(tg, tg_id, start=f"/start r_{code}")
    assert set(await _unlocks(db, A)) == {"money", "relationships", "karma"}
    calls = await tg.click(A, "sphere:karma")
    assert "Кармический хвост: 6-18-15" in sent_texts(calls)[0]
    assert alerts(await tg.click(A, "sphere:year")) == ["🔒 Эта сфера откроется за приглашения друзей: 2 друга"]
