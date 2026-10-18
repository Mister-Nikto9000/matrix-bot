"""Запускается из tests/test_startup.py в отдельном процессе: настоящий bot.main.main() с поддельным Telegram.

Отдельный процесс нужен, потому что роутеры бота — модульные синглтоны и уже подключены к диспетчеру тестов.
"""
import asyncio
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import GetMe, GetUpdates
from aiogram.types import User

from bot import main as bot_main
from bot.config import Settings


class PollingTelegram(BaseSession):
    def __init__(self):
        super().__init__()
        self.methods: list[str] = []

    async def make_request(self, bot, method, timeout=None):
        self.methods.append(type(method).__name__)
        if isinstance(method, GetUpdates):
            await asyncio.sleep(0.05)
            return []
        if isinstance(method, GetMe):
            return User(id=123, is_bot=True, first_name="bot", username="test_bot")
        return True

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        raise NotImplementedError


async def run(db_url: str) -> int:
    fake = PollingTelegram()
    settings = Settings(bot_token="123:test", database_url=db_url, bot_username="test_bot", admin_ids=[999])
    bot_main.get_settings = lambda: settings
    bot_main.Bot = lambda token, default=None: Bot(
        token, session=fake, default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    try:
        await asyncio.wait_for(bot_main.main(), timeout=2.0)
    except asyncio.TimeoutError:
        pass  # polling крутится, пока его не остановят — это и есть успешный запуск
    print("METHODS:", ",".join(sorted(set(fake.methods))))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run(sys.argv[1])))
