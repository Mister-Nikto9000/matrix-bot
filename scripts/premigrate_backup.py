"""Копия bot.db перед миграциями v2: backups/bot-before-v2.db.

Запускается из run.bat / run_forever.bat перед `alembic upgrade head`. Копия делается один раз и только если
база ещё не мигрирована на 0004 (иначе в «до» попали бы данные «после»).
Запуск вручную: python -m scripts.premigrate_backup
"""
import sqlite3
import sys
from pathlib import Path

from bot.config import get_settings
from bot.services import backup

V2_FIRST_REVISION = "0004"


def current_revision(db: Path) -> str | None:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.OperationalError:  # таблицы нет — база пустая или не под alembic
        return None
    finally:
        conn.close()
    return row[0] if row else None


def run(database_url: str, directory: Path = backup.BACKUP_DIR) -> str:
    """Возвращает человекочитаемый итог; исключения не бросает ради штатных ситуаций."""
    src = backup.sqlite_path(database_url)
    if src is None:
        return "Не SQLite: перед миграциями сделай бэкап средствами PostgreSQL (pg_dump)."
    dest = directory / backup.BEFORE_V2
    if dest.exists():
        return f"{dest} уже существует — пропускаю."
    if not src.exists():
        return f"{src} ещё нет — копировать нечего."
    revision = current_revision(src)
    if revision is not None and revision >= V2_FIRST_REVISION:
        return f"База уже на ревизии {revision} (v2) — копия «до v2» не нужна."
    backup.copy_db(src, dest)
    return f"Копия базы перед миграциями: {dest}"


def main() -> int:
    print(run(get_settings().database_url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
