"""Хостинг задаёт только DATA_DIR: база и бэкапы уходят в постоянную папку."""
from bot.config import Settings


def test_data_dir_sets_sqlite_path():
    s = Settings(_env_file=None, bot_token="1:x", data_dir="/app/data")
    assert s.database_url == "sqlite+aiosqlite:////app/data/bot.db"


def test_explicit_database_url_wins():
    s = Settings(_env_file=None, bot_token="1:x", data_dir="/app/data", database_url="sqlite+aiosqlite:///x.db")
    assert s.database_url == "sqlite+aiosqlite:///x.db"


def test_without_data_dir_default_unchanged():
    s = Settings(_env_file=None, bot_token="1:x")
    assert s.database_url.startswith("postgresql")
