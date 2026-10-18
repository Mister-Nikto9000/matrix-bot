"""Блок 9: логи с ротацией, бэкапы, /health, приватность, /deletedata, проверка каналов при запуске."""
import logging
import sqlite3
from datetime import date, datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text

from bot import jobs
from bot.db.models import Channel, DailyActivity, Event, PushLog, Quest, Unlock, User
from bot.main import LOG_BACKUPS, LOG_MAX_BYTES, setup_logging
from bot.services import alerts, backup, health, state
from scripts import premigrate_backup
from tests.conftest import sent_texts

ADMIN, U = 999, 9801


# --- Логи ---

def test_logging_rotates_5mb_x5(tmp_path):
    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    try:
        setup_logging(tmp_path / "logs" / "bot.log")
        handler = next(h for h in root.handlers if isinstance(h, RotatingFileHandler))
        assert (handler.maxBytes, handler.backupCount) == (5 * 1024 * 1024, 5) == (LOG_MAX_BYTES, LOG_BACKUPS)
        logging.getLogger("bot.test").info("проверка записи")
        handler.flush()
        assert "проверка записи" in (tmp_path / "logs" / "bot.log").read_text(encoding="utf-8")
    finally:
        for h in root.handlers[:]:
            if h not in old_handlers:
                h.close()
        root.handlers[:] = old_handlers
        root.setLevel(old_level)


# --- Бэкапы ---

def _make_db(path: Path, revision: str | None = None) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO users (name) VALUES ('Аня')")
    if revision:
        conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
        conn.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
    conn.commit()
    conn.close()
    return path


def test_sqlite_path_parsing():
    assert backup.sqlite_path("sqlite+aiosqlite:///bot.db") == Path("bot.db")
    assert backup.sqlite_path("sqlite+aiosqlite:////data/bot.db") == Path("/data/bot.db")
    assert backup.sqlite_path("postgresql+asyncpg://bot:bot@db/bot") is None
    assert backup.sqlite_path("sqlite+aiosqlite:///:memory:") is None


def test_daily_backup_creates_consistent_copy_once(tmp_path):
    src = _make_db(tmp_path / "bot.db")
    out = tmp_path / "backups"
    created = backup.daily_backup(src, date(2026, 10, 18), out)
    assert created == out / "bot-20261018.db"
    assert sqlite3.connect(created).execute("SELECT name FROM users").fetchall() == [("Аня",)]
    assert backup.daily_backup(src, date(2026, 10, 18), out) is None  # в тот же день повторно не делаем
    assert not list(out.glob("*.tmp"))


def test_backups_keep_only_seven_latest(tmp_path):
    src = _make_db(tmp_path / "bot.db")
    out = tmp_path / "backups"
    (out).mkdir()
    (out / backup.BEFORE_V2).write_bytes(b"keep me")
    for day in range(1, 11):
        backup.daily_backup(src, date(2026, 10, day), out)
    names = sorted(p.name for p in out.iterdir())
    assert names == sorted([backup.BEFORE_V2] + [f"bot-202610{d:02d}.db" for d in range(4, 11)])


async def test_backup_job_runs_through_scheduler(tg, db, tmp_path, monkeypatch):
    src = _make_db(tmp_path / "bot.db")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(jobs, "get_settings", lambda: type("S", (), {"database_url": "sqlite+aiosqlite:///bot.db"})())
    scheduler = jobs.build_scheduler(tg.bot, db)
    now = datetime(2026, 10, 18, 5, 0, tzinfo=timezone.utc)
    await scheduler.tick(now)
    assert (tmp_path / "backups" / "bot-20261018.db").exists() and src.exists()
    async with db() as s:
        assert await state.get(s, "last_backup") == "2026-10-18"


def test_premigrate_backup_cases(tmp_path):
    out = tmp_path / "backups"
    url = f"sqlite+aiosqlite:///{(tmp_path / 'bot.db').as_posix()}"
    assert "pg_dump" in premigrate_backup.run("postgresql+asyncpg://u:p@h/db", out)
    assert "нечего" in premigrate_backup.run(url, out)  # базы ещё нет

    _make_db(tmp_path / "bot.db", revision="0003")
    assert "Копия базы" in premigrate_backup.run(url, out)
    assert (out / "bot-before-v2.db").exists()
    assert "уже существует" in premigrate_backup.run(url, out)  # второй раз не перезаписываем

    (out / "bot-before-v2.db").unlink()
    conn = sqlite3.connect(tmp_path / "bot.db")
    conn.execute("UPDATE alembic_version SET version_num = '0004'")
    conn.commit()
    conn.close()
    assert "не нужна" in premigrate_backup.run(url, out)  # уже мигрирована — копия «до» не имеет смысла
    assert not (out / "bot-before-v2.db").exists()


def test_batch_files_exist_and_loop_with_five_second_pause():
    root = Path(__file__).resolve().parent.parent
    forever = (root / "run_forever.bat").read_text(encoding="utf-8")
    assert ":loop" in forever and "timeout /t 5" in forever and "goto loop" in forever
    assert "bot.main" in forever and "sleep" in forever.lower()
    assert "premigrate_backup" in (root / "run.bat").read_text(encoding="utf-8")
    assert "premigrate_backup" in forever


# --- /health ---

async def test_health_command(tg, db):
    await tg.send(U, "/start")
    text = sent_texts(await tg.send(ADMIN, "/health"))[0]
    for part in ("Аптайм", "Размер базы", "Последнее обновление", "Активных каналов: <b>0</b>", "Пользователей: <b>2</b>"):
        assert part in text
    assert await tg.send(U, "/health") == []  # только для админа


def test_human_formats():
    assert health.human_uptime(3 * 86400 + 5 * 3600 + 7 * 60) == "3 д 5 ч 7 мин"
    assert health.human_size(1536) == "1.5 КБ" and health.human_size(10) == "10 Б"


# --- Приватность ---

async def test_birth_date_not_stored_in_events(tg, db):
    await tg.send(U, "/start")
    await tg.send(U, "07.03.1994")
    async with db() as s:
        payloads = list(await s.scalars(select(Event.payload).where(Event.name == "birth_date_set")))
        everything = [str(p) for p in await s.scalars(select(Event.payload))]
    assert payloads == [None]
    assert not any("1994" in p for p in everything)


def test_migration_erases_dates_from_existing_events(tmp_path):
    db = tmp_path / "p.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db.as_posix()}")
    command.upgrade(cfg, "0007")
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    with engine.begin() as conn:
        for name, payload in (("birth_date_set", '{"date": "1994-03-07"}'), ("start", '{"args": "r_abc"}')):
            conn.execute(text("INSERT INTO events (name, payload, created_at) VALUES (:n, :p, '2026-10-01 10:00:00')"),
                         {"n": name, "p": payload})
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT name, payload FROM events")).all())
    engine.dispose()
    assert rows["birth_date_set"] is None and rows["start"] == '{"args": "r_abc"}'


async def test_deletedata_clears_personal_tables_but_counts_anonymously(tg, db):
    await tg.send(U, "/start")
    await tg.send(U, "07.03.1994")
    async with db() as s:
        user = await s.scalar(select(User).where(User.tg_id == U))
        user.notify_daily = True
        s.add_all([PushLog(user_id=user.id, kind="teaser_2h"),
                   Quest(user_id=user.id, day=date(2026, 10, 18), goal=1, progress=0, rewarded=False),
                   Unlock(user_id=user.id, item="money", source="gift")])
        await s.commit()
    await tg.send(U, "/deletedata")
    await tg.click(U, "delete_yes")
    async with db() as s:
        user = await s.scalar(select(User).where(User.tg_id == U))
        for model in (PushLog, Quest, DailyActivity, Unlock):
            assert not list(await s.scalars(select(model).where(model.user_id == user.id))), model.__name__
        assert user.birth_date is None and user.notify_daily is False
        assert await state.get(s, "deleted_data_count") == "1"
    assert "Удалили свои данные (/deletedata): <b>1</b>" in sent_texts(await tg.send(ADMIN, "/health"))[0]


# --- Проверка каналов при запуске ---

async def test_startup_check_warns_admins_when_bot_not_admin(tg, db):
    async with db() as s:
        s.add_all([
            Channel(chat_id=-1001, title="Рабочий", url="https://t.me/a", is_active=True, sort_order=1, joined_count=0),
            Channel(chat_id=-1002, title="Потерянный", url="https://t.me/b", is_active=True, sort_order=2, joined_count=0),
            Channel(chat_id=-1003, title="Выключенный", url="https://t.me/c", is_active=False, sort_order=3, joined_count=0),
        ])
        await s.commit()
    tg.fake.members[(-1001, tg.bot.id)] = "administrator+invite"
    assert await alerts.run_alerts(tg.bot, db) == 1
    warning = [t for t in sent_texts(tg.fake.calls, ADMIN) if "больше не администратор" in t]
    assert len(warning) == 1 and "Потерянный" in warning[0]  # рабочий и выключенный не упомянуты
