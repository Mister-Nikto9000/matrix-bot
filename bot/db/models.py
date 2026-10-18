from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# jsonb в PostgreSQL, обычный JSON в SQLite (для тестов)
JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    birth_date: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(1))  # f / m / NULL — ещё не выбрал(а)
    ref_code: Mapped[str] = mapped_column(String(16), unique=True)
    referrer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    channels_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # organic / ref / тег из s_<тег>; NULL — пользователь создан не через /start
    source: Mapped[str | None] = mapped_column(String(32))
    notify_daily: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    notify_marketing: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    notify_hour: Mapped[int] = mapped_column(Integer, default=9, server_default="9")


class Referral(Base):
    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inviter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    invited_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    kind: Mapped[str] = mapped_column(String(8))  # ref / comp
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counted: Mapped[bool] = mapped_column(Boolean, default=False)


class Unlock(Base):
    __tablename__ = "unlocks"
    __table_args__ = (UniqueConstraint("user_id", "item", name="uq_unlocks_user_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    item: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(16))  # gift / referral / admin
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompatPair(Base):
    __tablename__ = "compat_pairs"
    __table_args__ = (UniqueConstraint("user_a", "user_b", name="uq_compat_pairs_users"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Пара хранится упорядоченно: user_a < user_b
    user_a: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    user_b: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    title: Mapped[str] = mapped_column(String(256))
    url: Mapped[str] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    joined_count: Mapped[int] = mapped_column(Integer, default=0)
    # public — обычная подписка; request — закрытый канал со вступлением по заявке
    join_mode: Mapped[str] = mapped_column(String(16), default="public", server_default="public")
    # Бот сам одобряет заявки (нужно право can_invite_users)
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class JoinRequest(Base):
    """Заявка на вступление в канал. Для ОП заявка засчитывается как подписка."""

    __tablename__ = "join_requests"
    __table_args__ = (UniqueConstraint("tg_id", "chat_id", name="uq_join_requests_tg_chat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class DailyActivity(Base):
    """Один день — одна запись на пользователя; день считается по Москве."""

    __tablename__ = "daily_activity"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_daily_activity_user_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)


class ErrorLog(Base):
    """Необработанные ошибки бота (для отчёта по дням и алерта)."""

    __tablename__ = "errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(64))
    message: Mapped[str | None] = mapped_column(Text)


class BotState(Base):
    """Служебные значения: настройки дайджеста, время последних алертов и бэкапа."""

    __tablename__ = "bot_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PushLog(Base):
    """Отправленные пуши. unique(user_id, kind): каждый триггер уходит человеку один раз,
    ежедневное число дня — с датой в kind («daily:2026-10-18»)."""

    __tablename__ = "push_log"
    __table_args__ = (UniqueConstraint("user_id", "kind", name="uq_push_log_user_kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Quest(Base):
    """Квест дня: пригласить друга до 23:59 (по Москве) — награда: прогноз на завтра."""

    __tablename__ = "quests"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_quests_user_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    goal: Mapped[int] = mapped_column(Integer, default=1)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rewarded: Mapped[bool] = mapped_column(Boolean, default=False)


class Broadcast(Base):
    """Рассылка от админа: история и статистика."""

    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    created_by: Mapped[int] = mapped_column(BigInteger)  # Telegram ID админа
    text: Mapped[str] = mapped_column(Text)
    segment: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default="running")  # running / done / canceled / failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    sent: Mapped[int] = mapped_column(Integer, default=0)
    blocked: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
