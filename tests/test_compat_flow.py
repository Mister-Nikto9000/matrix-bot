"""Совместимость: ссылка c_<code>, результат обоим, подробная — только у открывших шкалой."""
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import func, select

from bot.content import messages as msg
from bot.db.models import CompatPair, Event, Unlock, User
from tests.conftest import buttons, sent, sent_texts

A, B, C = 3001, 3002, 3003


async def _user(db, tg_id) -> User:
    async with db() as s:
        return await s.scalar(select(User).where(User.tg_id == tg_id))


async def _setup_a(tg, db):
    await tg.send(A, "/start", first_name="Аня")
    await tg.send(A, "07.03.1994", first_name="Аня")  # E = 3
    return (await _user(db, A)).ref_code


async def test_compat_link(tg, db):
    code = await _setup_a(tg, db)
    calls = await tg.send(A, "/compat", first_name="Аня")
    text = sent_texts(calls)[0]
    assert f"https://t.me/test_bot?start=c_{code}" in text
    assert buttons(sent(calls)[0])[0].callback_data == "share:c"
    async with db() as s:
        assert await s.scalar(select(func.count()).select_from(Event).where(Event.name == "compat_link_created")) == 1


async def test_compat_pair_both_get_brief(tg, db):
    code = await _setup_a(tg, db)
    calls = await tg.send(B, f"/start c_{code}", first_name="Борис")
    # приглашение → вопрос об обращении (тест-клиент сам жмёт «Девушка») → просьба прислать дату
    assert sent_texts(calls) == ["💞 <b>Аня</b> хочет узнать вашу совместимость.", msg.GENDER_ASK, msg.ASK_DATE]

    calls = await tg.send(B, "23.11.1985", first_name="Борис")  # E = 6 → пара 9
    to_b = sent_texts(calls, B)
    assert "Матрица Судьбы" in to_b[1]
    assert "Совместимость: ты и Аня" in to_b[2] and "Главное число пары — 9. Отшельник" in to_b[2]
    assert msg.COMPAT_DETAIL_LOCKED in to_b[2]
    assert to_b[3] == msg.COMPAT_TRY_ANOTHER
    assert buttons(sent(calls, B)[3])[0].callback_data == "compat"

    to_a = sent_texts(calls, A)
    assert to_a[0].startswith("🎉 По твоей ссылке рассчитали матрицу!")  # c_ тоже делает пригласившим
    assert "Совместимость: ты и Борис" in to_a[1] and "9. Отшельник" in to_a[1]

    assert (await _user(db, B)).referrer_id == (await _user(db, A)).id
    async with db() as s:
        b_id = (await s.scalar(select(User).where(User.tg_id == B))).id
        assert await s.scalar(select(func.count()).select_from(CompatPair)) == 1
        # по c_ подарок «Деньги» не выдаётся
        assert list(await s.scalars(select(Unlock.item).where(Unlock.user_id == b_id))) == []


async def test_detailed_only_for_unlocked(tg, db):
    code = await _setup_a(tg, db)
    async with db() as s:
        a = await s.scalar(select(User).where(User.tg_id == A))
        s.add(Unlock(user_id=a.id, item="compat_full_and_year", source="admin"))
        await s.commit()
    await tg.send(B, f"/start c_{code}", first_name="Борис")
    calls = await tg.send(B, "23.11.1985", first_name="Борис")
    a_compat = sent_texts(calls, A)[-1]
    assert "Подробно по точкам пары" in a_compat
    # A: 7,3,5,15,3 + B: 5,11,5,21,6 → 12,14,10,9(36),9
    assert "Как вы проявляетесь вместе — 12." in a_compat and "Задача пары — 9." in a_compat
    assert "Подробно по точкам пары" not in sent_texts(calls, B)[2]


async def test_existing_user_gets_result_immediately(tg, db):
    code = await _setup_a(tg, db)
    await tg.send(C, "/start", first_name="Вера")
    await tg.send(C, "01.01.2001", first_name="Вера")
    calls = await tg.send(C, f"/start c_{code}", first_name="Вера")
    assert "Совместимость: ты и Аня" in sent_texts(calls, C)[0]
    assert "Совместимость: ты и Вера" in sent_texts(calls, A)[0]
    assert (await _user(db, C)).referrer_id is None  # уже был пользователем


async def test_own_compat_link(tg, db):
    code = await _setup_a(tg, db)
    assert sent_texts(await tg.send(A, f"/start c_{code}")) == [msg.COMPAT_SELF]


async def test_compat_survives_fsm_loss(tg, db):
    code = await _setup_a(tg, db)
    await tg.send(B, f"/start c_{code}", first_name="Борис")
    tg.dp.fsm.storage = MemoryStorage()  # «перезапуск» бота
    calls = await tg.send(B, "23.11.1985", first_name="Борис")
    assert any("Совместимость: ты и Аня" in t for t in sent_texts(calls, B))


async def test_inviter_without_date(tg, db):
    code = await _setup_a(tg, db)
    await tg.send(A, "/deletedata")
    await tg.click(A, "delete_yes")
    await tg.send(B, f"/start c_{code}", first_name="Борис")
    calls = await tg.send(B, "23.11.1985", first_name="Борис")
    # род — того, о ком речь: после /deletedata пол Ани не выбран → женская форма
    assert "Аня ещё не указала дату рождения" in sent_texts(calls, B)[-1]
