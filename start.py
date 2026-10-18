"""Единая точка запуска для хостингов: python start.py (бот сам делает бэкап SQLite и миграции при старте)."""
import asyncio

from bot.main import main

if __name__ == "__main__":
    asyncio.run(main())
