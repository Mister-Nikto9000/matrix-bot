"""Миграция Alembic должна создавать ту же схему, что описана в моделях."""
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from bot.db.models import Base


def test_migration_matches_models(tmp_path):
    db = tmp_path / "m.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db.as_posix()}")
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db.as_posix()}")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []

    command.downgrade(cfg, "base")


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_all_migrations_render_for_postgresql():
    """Офлайн-генерация SQL для PostgreSQL: миграции не используют ничего, что работает только в SQLite."""
    import io

    cfg = _cfg("postgresql+asyncpg://bot:bot@localhost/bot")
    cfg.output_buffer = io.StringIO()
    command.upgrade(cfg, "head", sql=True)
    sql = cfg.output_buffer.getvalue()
    for table in ("daily_activity", "errors", "bot_state"):
        assert f"CREATE TABLE {table}" in sql
    assert "ALTER TABLE users ADD COLUMN notify_marketing BOOLEAN DEFAULT true NOT NULL" in sql


def test_users_source_backfilled(tmp_path):
    """Существующие пользователи после 0004: приглашённые — ref, остальные — organic."""
    from sqlalchemy import text

    db = tmp_path / "b.db"
    cfg = _cfg(f"sqlite+aiosqlite:///{db.as_posix()}")
    command.upgrade(cfg, "0003")
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    with engine.begin() as conn:
        for uid, tg, ref in ((1, 101, None), (2, 102, 1)):
            conn.execute(text(
                "INSERT INTO users (id, tg_id, ref_code, referrer_id, created_at, is_blocked) "
                "VALUES (:i, :t, :c, :r, '2026-10-01 10:00:00', 0)"), {"i": uid, "t": tg, "c": f"code{uid}", "r": ref})
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT tg_id, source FROM users")).all())
        flags = conn.execute(text("SELECT notify_daily, notify_marketing, notify_hour FROM users WHERE id = 1")).one()
    engine.dispose()
    assert rows == {101: "organic", 102: "ref"}
    assert tuple(flags) == (0, 1, 9)
