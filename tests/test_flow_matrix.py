"""Сквозные сценарии: ввод даты, тизер, каналы, полный разбор, сферы, админка каналов."""
from sqlalchemy import select

from bot.content import messages as msg
from bot.db.models import Channel, User
from tests.conftest import alerts, buttons, sent, sent_texts

U = 1001
ADMIN = 999


async def _user(db, tg_id) -> User:
    async with db() as s:
        return await s.scalar(select(User).where(User.tg_id == tg_id))


async def test_start_and_date_validation(tg, db):
    calls = await tg.send(U, "/start")
    assert sent_texts(calls) == [msg.START_HELLO, msg.GENDER_ASK, msg.ASK_DATE]

    calls = await tg.send(U, "31.02.1990")
    assert sent_texts(calls) == [msg.DATE_INVALID]

    calls = await tg.send(U, "07.03.1994")
    texts = sent_texts(calls)
    assert "07.03.1994" in texts[0]
    assert "Личность — 7. Колесница" in texts[1]
    assert "Таланты — 3. Императрица" in texts[1]
    assert "Предназначение — 3. Императрица" in texts[1]
    assert [b.callback_data for b in buttons(sent(calls)[1])] == ["full", "compat", "invite"]

    user = await _user(db, U)
    assert str(user.birth_date) == "1994-03-07" and user.activated_at is not None

    # повторный /start показывает тизер
    assert "Матрица Судьбы" in sent_texts(await tg.send(U, "/start"))[0]


async def test_full_without_channels(tg, db):
    await tg.send(U, "/start")
    await tg.send(U, "07.03.1994")
    calls = await tg.click(U, "full")
    texts = sent_texts(calls)
    assert "Аня, вот твой полный разбор" in texts[0] and "Зона комфорта — 3. Императрица" in texts[0]
    assert "Небо — 18, Земля — 12" in texts[0]
    menu = buttons(sent(calls)[1])
    assert menu[0].text.startswith("🔒 Деньги — пригласи ещё 1 друга")
    assert menu[1].text.startswith("🔒 Отношения — пригласи ещё 2 друзей")
    assert (await _user(db, U)).channels_ok_at is not None


async def test_channels_gate(tg, db):
    async with db() as s:
        s.add(Channel(chat_id=-100500, title="Наш канал", url="https://t.me/our", is_active=True, sort_order=1, joined_count=0))
        await s.commit()
    await tg.send(U, "/start")
    await tg.send(U, "07.03.1994")

    calls = await tg.click(U, "full")
    assert sent_texts(calls) == [msg.CHANNELS_PROMPT]
    kb = buttons(sent(calls)[0])
    assert kb[0].url == "https://t.me/our" and kb[1].callback_data == "check_subs"

    calls = await tg.click(U, "check_subs")
    assert alerts(calls) == [msg.CHANNELS_STILL_MISSING] and not sent_texts(calls)

    tg.fake.members[(-100500, U)] = "member"
    calls = await tg.click(U, "check_subs")
    assert "полный разбор" in sent_texts(calls)[0]
    async with db() as s:
        assert (await s.scalar(select(Channel))).joined_count == 1
    assert (await _user(db, U)).channels_ok_at is not None


async def test_locked_sphere_alert(tg):
    await tg.send(U, "/start")
    await tg.send(U, "07.03.1994")
    calls = await tg.click(U, "sphere:karma")
    assert alerts(calls) == ["🔒 Эта сфера откроется за приглашения друзей: 3 друга"]


async def test_deletedata(tg, db):
    await tg.send(U, "/start")
    await tg.send(U, "07.03.1994")
    await tg.send(U, "/deletedata")
    await tg.click(U, "delete_yes")
    user = await _user(db, U)
    assert user.birth_date is None and user.gender is None
    calls = await tg.send(U, "01.01.2001")
    # пол очищен — сначала вопрос, потом присланная дата обрабатывается
    assert sent_texts(calls)[0] == msg.GENDER_ASK
    assert "01.01.2001" in sent_texts(calls)[1]


async def test_admin_channels(tg, db):
    tg.fake.chats["@our"] = -100777
    calls = await tg.send(ADMIN, "/addchannel @our https://t.me/our")
    assert "не администратор" in sent_texts(calls)[0]

    tg.fake.members[(-100777, tg.bot.id)] = "administrator"
    calls = await tg.send(ADMIN, "/addchannel @our https://t.me/our")
    assert "добавлен" in sent_texts(calls)[0]
    calls = await tg.send(ADMIN, "/channels")
    assert "#1 Канал -100777" in sent_texts(calls)[0]

    calls = await tg.send(ADMIN, "/addchannel @missing https://t.me/x")
    assert "не найден" in sent_texts(calls)[0]

    # не-админу команды не отвечают
    assert sent_texts(await tg.send(U, "/channels")) == []

    calls = await tg.send(ADMIN, "/delchannel 1")
    assert "удалён" in sent_texts(calls)[0]
    async with db() as s:
        assert await s.scalar(select(Channel)) is None
