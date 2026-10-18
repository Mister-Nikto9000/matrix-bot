"""Шлюз подписки: сферы, сторис и ежедневные кнопки проверяют каналы при каждом нажатии."""
from sqlalchemy import select

from bot.content import messages as msg
from bot.db.models import Channel, User
from bot.middlewares import needs_subscription
from tests.conftest import buttons, sent, sent_texts

U = 1001
CH = -1001


async def _setup(tg, db, subscribed=True):
    async with db() as s:
        s.add(Channel(chat_id=CH, title="К", url="https://t.me/k", is_active=True, sort_order=1, joined_count=0))
        await s.commit()
    await tg.send(U, "/start")
    await tg.send(U, "07.03.1994")
    if subscribed:
        tg.fake.members[(CH, U)] = "member"
    await tg.click(U, "full")


def test_gated_callbacks():
    for data in ("sphere:money", "story", "pc:daily:today", "pc:friend:sphere_money", "pc:x:spheres"):
        assert needs_subscription(data), data
    for data in ("full", "check_subs", "invite", "pc:teaser_2h:full", "pc:x:teaser", "gender:f", "daily:on", None):
        assert not needs_subscription(data), data


async def test_lapsed_subscription_blocks_sphere_and_story(tg, db):
    await _setup(tg, db)
    tg.fake.members[(CH, U)] = "left"
    from bot.services import channels
    channels._cache.clear()
    for data in ("sphere:money", "story"):
        calls = await tg.click(U, data)
        assert sent_texts(calls) == [msg.CHANNELS_LAPSED]
        assert buttons(sent(calls)[0])[-1].callback_data == "check_subs"


async def test_returning_user_gets_menu_not_full_again(tg, db):
    await _setup(tg, db)
    tg.fake.members[(CH, U)] = "left"
    from bot.services import channels
    channels._cache.clear()
    await tg.click(U, "sphere:money")
    tg.fake.members[(CH, U)] = "member"
    calls = await tg.click(U, "check_subs")
    texts = sent_texts(calls)
    assert texts[0] == msg.CHANNELS_BACK and len(texts) == 2


async def test_no_channels_means_no_gate(tg, db):
    await tg.send(U, "/start")
    await tg.send(U, "07.03.1994")
    calls = await tg.click(U, "sphere:personality")
    assert msg.CHANNELS_LAPSED not in sent_texts(calls)
