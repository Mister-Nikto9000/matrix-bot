"""Резервные копии SQLite через встроенный sqlite3.backup (безопасно при работающем боте).

- ежедневно: backups/bot-ГГГГММДД.db, хранится 7 последних;
- один раз перед миграциями v2: backups/bot-before-v2.db (см. scripts/premigrate_backup.py).
PostgreSQL бэкапится средствами самой СУБД (pg_dump), здесь ничего не делаем.
"""
import logging
import os
import re
import sqlite3
from datetime import date
from pathlib import Path

from sqlalchemy.engine import make_url

log = logging.getLogger(__name__)

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "backups"))  # на хостинге — внутри постоянной папки, например data/backups
KEEP = 7
DAILY_NAME = re.compile(r"^bot-(\d{8})\.db$")
BEFORE_V2 = "bot-before-v2.db"


def sqlite_path(database_url: str) -> Path | None:
    """Путь к файлу SQLite из DATABASE_URL; None — если это не SQLite (или память)."""
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return None
    return Path(url.database)


def copy_db(src: Path, dest: Path) -> Path:
    """Консистентная копия базы встроенным механизмом backup."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    source = sqlite3.connect(src)
    target = sqlite3.connect(tmp)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    tmp.replace(dest)
    return dest


def daily_path(day: date, directory: Path = BACKUP_DIR) -> Path:
    return directory / f"bot-{day:%Y%m%d}.db"


def prune(directory: Path = BACKUP_DIR, keep: int = KEEP) -> list[Path]:
    """Оставляет `keep` самых свежих ежедневных копий; bot-before-v2.db не трогает. Возвращает удалённые."""
    daily = sorted(p for p in directory.glob("bot-*.db") if DAILY_NAME.match(p.name))
    removed = daily[:-keep] if keep else daily
    for p in removed:
        p.unlink(missing_ok=True)
    return removed


def daily_backup(src: Path, day: date, directory: Path = BACKUP_DIR) -> Path | None:
    """Делает копию за день, если её ещё нет. Возвращает путь, если создана."""
    dest = daily_path(day, directory)
    if dest.exists() or not src.exists():
        return None
    copy_db(src, dest)
    prune(directory)
    log.info("SQLite backup created: %s", dest)
    return dest
