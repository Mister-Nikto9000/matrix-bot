"""Время: в БД всё хранится в UTC, а «сегодня», «вчера» и тихие часы считаются по Москве."""
from datetime import date, datetime, time, timedelta, timezone

from bot.config import MSK


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite отдаёт даты без часового пояса — в них лежит UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def to_msk(dt: datetime) -> datetime:
    return as_utc(dt).astimezone(MSK)


def msk_day(dt: datetime) -> date:
    """Календарный день по Москве для момента dt."""
    return to_msk(dt).date()


def msk_today(now: datetime | None = None) -> date:
    return msk_day(now or now_utc())


def day_start_utc(day: date) -> datetime:
    """Начало московских суток в UTC (с tzinfo=UTC: SQLite хранит время как UTC-часы)."""
    return datetime.combine(day, time.min, tzinfo=MSK).astimezone(timezone.utc)


def day_range_utc(day: date) -> tuple[datetime, datetime]:
    return day_start_utc(day), day_start_utc(day + timedelta(days=1))
