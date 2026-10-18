"""Автомиграция при старте бота: бэкап SQLite (если база старше версии 2) и alembic upgrade head.

Нужна хостингам, которые запускают только `python -m bot.main` / `main.py`. Отключается AUTO_MIGRATE=0.
"""
import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]


def _upgrade(database_url: str) -> None:
    try:
        from scripts import premigrate_backup

        premigrate_backup.run(database_url)
    except Exception:  # noqa: BLE001 — бэкап не должен мешать запуску
        log.exception("premigrate backup failed")
    cfg = Config()  # без alembic.ini: его fileConfig переписал бы логирование бота (logs/bot.log)
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))  # configparser: % — служебный символ
    command.upgrade(cfg, "head")


async def upgrade_to_head(database_url: str) -> None:
    log.info("Applying migrations")
    await asyncio.to_thread(_upgrade, database_url)
