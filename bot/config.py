from datetime import timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    admin_ids: Annotated[list[int], NoDecode] = []
    database_url: str = "postgresql+asyncpg://bot:bot@db:5432/bot"
    bot_username: str = ""
    data_dir: str = ""  # постоянная папка хостинга (Bothost задаёт DATA_DIR=/app/data)

    @model_validator(mode="after")
    def _default_db_in_data_dir(self):
        """DATABASE_URL не задан, а хостинг дал постоянную папку — база SQLite лежит в ней."""
        if self.data_dir and "database_url" not in self.model_fields_set:
            self.database_url = f"sqlite+aiosqlite:///{self.data_dir.rstrip('/')}/bot.db"
        return self

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _split_ids(cls, v):
        if isinstance(v, str):
            return [int(x) for x in v.replace(" ", "").split(",") if x]
        if isinstance(v, int):
            return [v]
        return v

    @field_validator("bot_username", mode="before")
    @classmethod
    def _strip_at(cls, v):
        return (v or "").strip().lstrip("@")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


# --- Шкала приглашений ---
# Каждый достигнутый порог открывает следующий ещё закрытый пункт из REWARD_ORDER.
REWARD_THRESHOLDS = [1, 2, 3, 5, 7, 10]
REWARD_ORDER = ["money", "relationships", "karma", "compat_full_and_year", "practices", "ai"]

# Подарок приглашённому по ссылке r_<code>
GIFT_ITEM = "money"

# Антифрод: сколько активаций за последние 24 часа засчитывается одному пригласившему
DAILY_ACTIVATION_LIMIT = 20

# Кэш положительных проверок подписки на каналы, секунд
CHANNEL_CACHE_TTL = 600

# Допустимый диапазон года рождения (верхняя граница — текущий год)
MIN_BIRTH_YEAR = 1920


# --- Время ---
# «Сегодня», «вчера», тихие часы и рассылки считаются по Москве, а не по UTC
TIMEZONE = "Europe/Moscow"
try:
    MSK = ZoneInfo(TIMEZONE)
except ZoneInfoNotFoundError:  # на Windows без пакета tzdata; в Москве круглый год UTC+3
    MSK = timezone(timedelta(hours=3), "MSK")
