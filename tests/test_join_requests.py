"""Каналы со вступлением по заявке: заявка засчитывается как подписка."""
from aiogram.methods import ApproveChatJoinRequest
from sqlalchemy import func, select

from bot.content import messages as msg
from bot.db.models import Channel, JoinRequest, User
from tests.conftest import alerts, buttons, sent, sent_texts

U = 1001
ADMIN = 999
CLOSED = -100900


async def _closed_channel(db, auto_approve=False):
    async with db() as s:
        s.add(Channel(chat_id=CLOSED, title="Закрытый", url="https://t.me/+abc", is_active=True, sort_order=1,
                      joined_count=0, join_mode="request", auto_approve=auto_approve))
        await s.commit()


async def _ready(tg):
    await tg.send(U, "/start")
    await tg.send(U, "07.03.1994")


async def test_prompt_for_request_channel(tg, db):
    await _closed_channel(db)
    await _ready(tg)
    calls = await tg.click(U, "full")
    assert sent_texts(calls) == [msg.CHANNELS_PROMPT + msg.CHANNELS_REQUEST_HINT]
    assert "одобрения ждать не нужно" in sent_texts(calls)[0]
    kb = buttons(sent(calls)[0])
    assert kb[0].text == "📨 Подать заявку: Закрытый" and kb[0].url == "https://t.me/+abc"


async def test_without_request_and_membership_not_counted(tg, db):
    await _closed_channel(db)
    await _ready(tg)
    calls = await tg.click(U, "check_subs")
    assert alerts(calls) == [msg.CHANNELS_STILL_MISSING]
    # заявка в чужой канал не засчитывается
    await tg.join_request(U, -100111)
    assert alerts(await tg.click(U, "check_subs")) == [msg.CHANNELS_STILL_MISSING]


async def test_request_counts_as_subscription(tg, db):
    await _closed_channel(db)
    await _ready(tg)
    await tg.click(U, "full")
    calls = await tg.join_request(U, CLOSED)
    assert not any(isinstance(c, ApproveChatJoinRequest) for c in calls)  # автоодобрение выключено
    await tg.join_request(U, CLOSED)  # повторная заявка не дублируется

    calls = await tg.click(U, "check_subs")
    assert "полный разбор" in sent_texts(calls)[0]
    async with db() as s:
        assert await s.scalar(select(func.count()).select_from(JoinRequest)) == 1
        assert (await s.scalar(select(Channel))).joined_count == 1
        # заявка не создаёт пользователя бота сама по себе
        assert await s.scalar(select(func.count()).select_from(User)) == 1


async def test_request_from_unknown_user_does_not_create_user(tg, db):
    await _closed_channel(db)
    await tg.join_request(5555, CLOSED)
    async with db() as s:
        assert await s.scalar(select(func.count()).select_from(User)) == 0
        assert await s.scalar(select(func.count()).select_from(JoinRequest)) == 1


async def test_auto_approve(tg, db):
    await _closed_channel(db, auto_approve=True)
    calls = await tg.join_request(U, CLOSED)
    approve = [c for c in calls if isinstance(c, ApproveChatJoinRequest)]
    assert len(approve) == 1 and approve[0].chat_id == CLOSED and approve[0].user_id == U


async def test_admin_request_channel(tg, db):
    tg.fake.chats["@closed"] = CLOSED
    tg.fake.members[(CLOSED, tg.bot.id)] = "administrator"
    calls = await tg.send(ADMIN, "/addchannel @closed https://t.me/+abc request")
    assert "can_invite_users" in sent_texts(calls)[0]

    tg.fake.members[(CLOSED, tg.bot.id)] = "administrator+invite"
    calls = await tg.send(ADMIN, "/addchannel @closed https://t.me/+abc request")
    assert "добавлен" in sent_texts(calls)[0]
    async with db() as s:
        ch = await s.scalar(select(Channel))
        assert ch.join_mode == "request" and ch.auto_approve is False

    await tg.join_request(U, CLOSED)
    text = sent_texts(await tg.send(ADMIN, "/channels"))[0]
    assert "по заявкам: 1, автоодобрение выкл" in text

    assert "включено" in sent_texts(await tg.send(ADMIN, "/approve 1 on"))[0]
    assert "автоодобрение вкл" in sent_texts(await tg.send(ADMIN, "/channels"))[0]
    assert "выключено" in sent_texts(await tg.send(ADMIN, "/approve 1 off"))[0]
    assert "Использование" in sent_texts(await tg.send(ADMIN, "/approve 1 maybe"))[0]
    assert "Использование" in sent_texts(await tg.send(ADMIN, "/addchannel @closed https://t.me/+abc private"))[0]


async def test_approve_rejects_public_channel(tg, db):
    async with db() as s:
        s.add(Channel(chat_id=-100500, title="Открытый", url="https://t.me/our", is_active=True, sort_order=1,
                      joined_count=0))
        await s.commit()
    assert "открытый" in sent_texts(await tg.send(ADMIN, "/approve 1 on"))[0]
    assert "открытый" in sent_texts(await tg.send(ADMIN, "/channels"))[0]
