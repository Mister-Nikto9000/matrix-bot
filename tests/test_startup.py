"""Запуск бота целиком (без сети): миграции → настоящий bot.main.main() → long polling с нужными allowed_updates."""
import asyncio
import subprocess
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parent.parent


async def test_bot_starts_on_migrated_database_and_polls_inline_updates(tmp_path):
    db_url = f"sqlite+aiosqlite:///{(tmp_path / 'bot.db').as_posix()}"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")  # боевая схема через миграции, а не create_all
    await _run_and_check(tmp_path, db_url)


async def test_bot_migrates_empty_database_on_start(tmp_path):
    """Хостинг запускает только python -m bot.main: бот сам применяет миграции к пустой базе."""
    db_url = f"sqlite+aiosqlite:///{(tmp_path / 'bot.db').as_posix()}"
    await _run_and_check(tmp_path, db_url)
    import sqlite3

    tables = {r[0] for r in sqlite3.connect(tmp_path / "bot.db").execute("select name from sqlite_master")}
    assert {"users", "push_log", "quests", "broadcasts", "alembic_version"} <= tables


async def _run_and_check(tmp_path, db_url):

    proc = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "tests._startup_runner", db_url],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
        env={"PYTHONPATH": str(ROOT), "PATH": "", "SYSTEMROOT": ""},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    log_text = (tmp_path / "logs" / "bot.log").read_text(encoding="utf-8")

    assert "Bot @test_bot started (long polling)" in log_text
    allowed_line = next(line for line in log_text.splitlines() if "allowed_updates:" in line)
    for update_type in ("message", "callback_query", "inline_query", "chosen_inline_result", "chat_join_request",
                        "my_chat_member"):
        assert update_type in allowed_line, update_type
    assert "ERROR" not in log_text and "Traceback" not in log_text, log_text
    methods = proc.stdout.split("METHODS:")[1]
    for method in ("GetUpdates", "SetMyCommands", "SetMyDescription"):
        assert method in methods
