import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, ErrorEvent

from bot.config import get_settings
from bot.content import messages as msg
from bot.db.session import make_engine, make_sessionmaker
from bot.db.models import ErrorLog
from bot.handlers import (
    admin,
    admin_broadcast,
    admin_stats,
    admin_tools,
    channel_admin,
    channels,
    common,
    compat,
    group,
    inline,
    invite,
    matrix,
    notifications,
    start,
    story,
)
from bot.jobs import build_scheduler
from bot.services import alerts, health
from bot.middlewares import DbMiddleware, RenderMiddleware

log = logging.getLogger("bot")

COMMANDS = [
    BotCommand(command="start", description="Начать"),
    BotCommand(command="matrix", description="Моя матрица"),
    BotCommand(command="compat", description="Совместимость"),
    BotCommand(command="invite", description="Пригласить друзей"),
    BotCommand(command="settings", description="Настройки и число дня"),
    BotCommand(command="stop", description="Отключить напоминания"),
    BotCommand(command="help", description="Помощь"),
    BotCommand(command="deletedata", description="Удалить мои данные"),
]

GROUP_COMMANDS = [BotCommand(command="matrix", description="Узнать свой аркан")]


async def record_error(sessionmaker, exc: BaseException) -> None:
    """Пишет ошибку в таблицу errors для отчёта и алерта; сбой записи не должен мешать ответу пользователю."""
    if sessionmaker is None:
        return
    try:
        async with sessionmaker() as session:
            session.add(ErrorLog(kind=type(exc).__name__, message=str(exc)[:500]))
            await session.commit()
    except Exception:  # noqa: BLE001
        log.exception("Failed to record error")


def make_on_error(db_mw: DbMiddleware):
    async def on_error(event: ErrorEvent) -> bool:
        await record_error(db_mw.sessionmaker, event.exception)
        return await notify_about_error(event)

    return on_error


async def notify_about_error(event: ErrorEvent) -> bool:
    log.exception("Unhandled error: %s", event.exception, exc_info=event.exception)
    update = event.update
    try:
        if update.message:
            await update.message.answer(msg.ERROR)
        elif update.callback_query:
            await update.callback_query.answer()
            if update.callback_query.message:
                await update.callback_query.message.answer(msg.ERROR)
    except Exception:  # noqa: BLE001 — не даём обработчику ошибок упасть
        log.exception("Failed to notify user about error")
    return True


async def setup_profile(bot: Bot) -> None:
    """Описание бота в профиле. Ошибка (например, лимит запросов) не мешает запуску."""
    try:
        await bot.set_my_description(msg.BOT_DESCRIPTION)
        await bot.set_my_short_description(msg.BOT_SHORT_DESCRIPTION)
    except TelegramAPIError as e:
        log.warning("Failed to set bot description: %s", e)


def build_dispatcher(db_mw: DbMiddleware) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(db_mw)
    dp.callback_query.middleware(db_mw)
    dp.chat_join_request.middleware(db_mw)
    dp.my_chat_member.middleware(db_mw)
    dp.inline_query.middleware(db_mw)
    dp.chosen_inline_result.middleware(db_mw)
    dp.errors.register(make_on_error(db_mw))
    # start.router — последним: в нём перехват свободного текста (ввод даты)
    dp.include_routers(
        group.router,  # группы — первыми: личные сценарии в группах не должны срабатывать
        admin.router, admin_stats.router, admin_tools.router, admin_broadcast.router, channel_admin.router,
        common.router, notifications.router, inline.router, story.router, matrix.router, channels.router,
        invite.router, compat.router, start.router,
    )
    return dp


LOG_FILE = Path("logs") / "bot.log"
LOG_MAX_BYTES, LOG_BACKUPS = 5 * 1024 * 1024, 5  # 5 МБ × 5 файлов


def setup_logging(log_file: Path = LOG_FILE) -> None:
    """Лог в консоль и в logs/bot.log с ротацией (токен и даты рождения в лог не попадают)."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
    console = logging.StreamHandler(sys.stdout)
    for handler in (file_handler, console):
        handler.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console], force=True)
    logging.getLogger("aiogram").setLevel(logging.INFO)


async def main() -> None:
    setup_logging()
    health.mark_started()
    settings = get_settings()

    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session.middleware(RenderMiddleware())  # формы {м|ж} по полу получателя
    if not settings.bot_username:
        settings.bot_username = (await bot.get_me()).username or ""

    dp = build_dispatcher(DbMiddleware(sessionmaker))
    await bot.set_my_commands(COMMANDS)
    await bot.set_my_commands(GROUP_COMMANDS, scope=BotCommandScopeAllGroupChats())
    await setup_profile(bot)
    log.info("Bot @%s started (long polling)", settings.bot_username)
    scheduler = build_scheduler(bot, sessionmaker)
    scheduler.start()
    try:  # бот должен быть админом во всех активных каналах — иначе предупреждаем админов сразу при запуске
        await alerts.run_alerts(bot, sessionmaker)
    except Exception:  # noqa: BLE001 — проверка не должна мешать запуску
        log.exception("Startup channel check failed")
    try:
        # chat_join_request и my_chat_member не приходят по умолчанию — явно перечисляем используемые апдейты
        allowed = dp.resolve_used_update_types()
        log.info("allowed_updates: %s", ", ".join(allowed))
        await dp.start_polling(bot, allowed_updates=allowed)
    finally:
        await scheduler.stop()
        await engine.dispose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
