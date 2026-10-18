"""Профиль бота (блок 6)."""
from aiogram.methods import SetMyDescription, SetMyShortDescription

from bot.content import messages as msg
from bot.main import setup_profile


def test_description_limits():
    assert len(msg.BOT_DESCRIPTION) <= 512
    assert len(msg.BOT_SHORT_DESCRIPTION) <= 120
    for text in (msg.BOT_DESCRIPTION, msg.BOT_SHORT_DESCRIPTION):
        assert "для развлечения и самопознания" in text.lower()
        assert "<" not in text  # описание не поддерживает HTML


async def test_setup_profile(tg):
    await setup_profile(tg.bot)
    calls = tg.fake.calls
    assert [c.description for c in calls if isinstance(c, SetMyDescription)] == [msg.BOT_DESCRIPTION]
    assert [c.short_description for c in calls if isinstance(c, SetMyShortDescription)] == [
        msg.BOT_SHORT_DESCRIPTION
    ]


def test_join_request_in_allowed_updates():
    from tests.conftest import _dp

    assert "chat_join_request" in _dp.resolve_used_update_types()
