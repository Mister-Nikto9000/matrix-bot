"""Подключение каналов кнопками: my_chat_member, пересылка поста, управление в /channels."""
from aiogram.methods import CreateChatInviteLink, EditMessageReplyMarkup, EditMessageText
from sqlalchemy import select

from bot.content import messages as msg
from bot.db.models import Channel, User
from tests.conftest import _dp, alerts, buttons, sent, sent_texts

ADMIN, U = 999, 1001
CH = -100123


async def _channels(db) -> list[Channel]:
    async with db() as s:
        return list(await s.scalars(select(Channel).order_by(Channel.sort_order)))


def _edits(calls) -> list[EditMessageText]:
    return [c for c in calls if isinstance(c, EditMessageText)]


def test_my_chat_member_in_allowed_updates():
    assert "my_chat_member" in _dp.resolve_used_update_types()


async def test_added_as_admin_offer_and_connect(tg, db):
    tg.fake.members[(CH, tg.bot.id)] = "administrator+invite"
    tg.fake.titles[CH] = "Нумерология"
    calls = await tg.bot_status(CH, "Нумерология", "left", "administrator")
    offer = sent(calls, ADMIN)
    assert [m.text for m in offer] == [
        "Меня добавили админом в канал «Нумерология». Подключить его к обязательной подписке?"
    ]
    assert [b.callback_data for b in buttons(offer[0])] == [
        f"chadd:public:{CH}", f"chadd:request:{CH}", f"chadd:skip:{CH}"
    ]
    assert [b.text for b in buttons(offer[0])] == ["✅ Обычный вход", "📨 Вход по заявке", "Не подключать"]
    async with db() as s:  # владелец канала, добавивший бота, не становится пользователем бота
        assert await s.scalar(select(User)) is None

    calls = await tg.click(ADMIN, f"chadd:request:{CH}")
    link = [c for c in calls if isinstance(c, CreateChatInviteLink)][0]
    assert link.chat_id == CH and link.name == "Матрица Судьбы" and link.creates_join_request is True
    text = sent_texts(calls, ADMIN)[0]
    assert text.startswith("Канал «Нумерология» подключён: вход по заявке. Ссылка: https://t.me/+link")
    [ch] = await _channels(db)
    assert (ch.chat_id, ch.title, ch.join_mode, ch.is_active) == (CH, "Нумерология", "request", True)
    assert ch.url in text


async def test_public_mode_link_without_request(tg, db):
    tg.fake.members[(CH, tg.bot.id)] = "administrator+invite"
    calls = await tg.click(ADMIN, f"chadd:public:{CH}")
    link = [c for c in calls if isinstance(c, CreateChatInviteLink)][0]
    assert link.creates_join_request is False
    assert "подключён: обычный вход" in sent_texts(calls)[0]
    assert (await _channels(db))[0].join_mode == "public"


async def test_skip(tg, db):
    calls = await tg.click(ADMIN, f"chadd:skip:{CH}")
    assert sent_texts(calls) == [msg.ADMIN_CHANNEL_SKIPPED]
    assert await _channels(db) == []


async def test_no_invite_right_not_connected(tg, db):
    tg.fake.members[(CH, tg.bot.id)] = "administrator"  # админ, но без «Пригласительных ссылок»
    calls = await tg.click(ADMIN, f"chadd:public:{CH}")
    assert "«Пригласительные ссылки»" in sent_texts(calls)[0] and "не подключён" in sent_texts(calls)[0]
    assert not any(isinstance(c, EditMessageReplyMarkup) for c in calls)  # кнопки остаются — нажать ещё раз
    assert await _channels(db) == []


async def test_forward_from_channel(tg, db):
    calls = await tg.forward(ADMIN, CH, "Мой канал")
    assert "не администратор" in sent_texts(calls)[0] and "«Пригласительные ссылки»" in sent_texts(calls)[0]

    tg.fake.members[(CH, tg.bot.id)] = "administrator+invite"
    calls = await tg.forward(ADMIN, CH, "Мой канал")
    assert sent_texts(calls) == ["Канал «Мой канал». Подключить его к обязательной подписке?"]
    assert len(buttons(sent(calls)[0])) == 3

    tg.fake.titles[CH] = "Мой канал"
    await tg.click(ADMIN, f"chadd:public:{CH}")
    calls = await tg.forward(ADMIN, CH, "Мой канал")  # уже подключён — текущие настройки и смена режима
    text = sent_texts(calls)[0]
    assert "уже подключён: обычный вход, активен" in text and "Изменить режим" in text
    assert [b.text for b in buttons(sent(calls)[0])][-1] == msg.BTN_CH_KEEP

    calls = await tg.click(ADMIN, f"chadd:request:{CH}")
    assert "подключён: вход по заявке" in sent_texts(calls)[0]
    [ch] = await _channels(db)
    assert ch.join_mode == "request"


async def test_bot_removed_disables_channel(tg, db):
    tg.fake.members[(CH, tg.bot.id)] = "administrator+invite"
    await tg.click(ADMIN, f"chadd:public:{CH}")
    calls = await tg.bot_status(CH, "Нумерология", "administrator", "left")
    assert sent_texts(calls, ADMIN) == [
        "Меня убрали из админов канала «Нумерология» — проверка подписки на него выключена"
    ]
    assert (await _channels(db))[0].is_active is False
    # повторно не пишем; чужие каналы не трогаем
    assert sent_texts(await tg.bot_status(CH, "Нумерология", "administrator", "kicked")) == []
    assert sent_texts(await tg.bot_status(-100555, "Чужой", "administrator", "member")) == []


async def test_channels_management(tg, db):
    tg.fake.members[(CH, tg.bot.id)] = "administrator+invite"
    tg.fake.members[(-100200, tg.bot.id)] = "administrator+invite"
    await tg.click(ADMIN, f"chadd:public:{CH}")
    await tg.click(ADMIN, "chadd:request:-100200")

    calls = await tg.send(ADMIN, "/channels")
    msgs = sent(calls)
    assert len(msgs) == 3 and msgs[-1].text == msg.ADMIN_CHANNELS_HINT
    assert [b.text for b in buttons(msgs[0])] == ["✅ Вкл", "Режим: обычный", "⬆️ Вверх", "⬇️ Вниз", "🗑 Удалить"]
    assert [b.text for b in buttons(msgs[1])] == [
        "✅ Вкл", "Режим: заявка", "Автоодобрение: выкл", "⬆️ Вверх", "⬇️ Вниз", "🗑 Удалить"
    ]

    # вкл/выкл
    calls = await tg.click(ADMIN, "ch:toggle:1")
    assert "выключен" in _edits(calls)[0].text
    assert (await _channels(db))[0].is_active is False
    await tg.click(ADMIN, "ch:toggle:1")

    # смена режима — бот создаёт новую ссылку
    calls = await tg.click(ADMIN, "ch:mode:1")
    assert [c.creates_join_request for c in calls if isinstance(c, CreateChatInviteLink)] == [True]
    ch1 = (await _channels(db))[0]
    assert ch1.join_mode == "request" and ch1.url in _edits(calls)[0].text

    # без права на ссылки режим не меняется
    tg.fake.members[(CH, tg.bot.id)] = "administrator"
    calls = await tg.click(ADMIN, "ch:mode:1")
    assert alerts(calls) == [msg.ADMIN_NO_INVITE_RIGHT_ALERT]
    assert (await _channels(db))[0].join_mode == "request"

    # автоодобрение
    calls = await tg.click(ADMIN, "ch:auto:2")
    assert "автоодобрение вкл" in _edits(calls)[0].text
    assert (await _channels(db))[1].auto_approve is True

    # порядок
    await tg.click(ADMIN, "ch:down:1")
    assert [c.id for c in await _channels(db)] == [2, 1]
    calls = await tg.click(ADMIN, "ch:down:1")
    assert _edits(calls) == []  # уже последний
    await tg.click(ADMIN, "ch:up:1")
    assert [c.id for c in await _channels(db)] == [1, 2]

    # удаление с подтверждением
    calls = await tg.click(ADMIN, "ch:del:2")
    assert _edits(calls)[0].text.startswith("Удалить канал")
    assert len(await _channels(db)) == 2
    calls = await tg.click(ADMIN, "ch:delno:2")
    assert "Режим" in buttons(_edits(calls)[0])[1].text
    await tg.click(ADMIN, "ch:del:2")
    calls = await tg.click(ADMIN, "ch:delyes:2")
    assert "удалён" in _edits(calls)[0].text
    assert [c.id for c in await _channels(db)] == [1]


async def test_non_admin_cannot_manage(tg, db):
    tg.fake.members[(CH, tg.bot.id)] = "administrator+invite"
    await tg.click(ADMIN, f"chadd:public:{CH}")
    await tg.send(U, "/start")
    await tg.send(U, "07.03.1994")

    for data in ("ch:toggle:1", "ch:mode:1", "ch:del:1", "ch:delyes:1", "ch:up:1", f"chadd:request:{CH}",
                 "chadd:public:-100999"):
        calls = await tg.click(U, data)
        assert not any(isinstance(c, (EditMessageText, CreateChatInviteLink)) for c in calls)
        assert sent_texts(calls) == []
    calls = await tg.forward(U, CH, "Нумерология", text="пост")
    assert sent_texts(calls) == [msg.USE_BUTTONS]  # как любой текст от пользователя
    assert sent_texts(await tg.send(U, "/channels")) == []

    [ch] = await _channels(db)
    assert (ch.is_active, ch.join_mode) == (True, "public")
