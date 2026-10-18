"""Уведомления админам (ADMIN_IDS)."""
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup

from bot.config import get_settings

log = logging.getLogger(__name__)


async def notify_admins(
    bot: Bot, text: str, reply_markup: InlineKeyboardMarkup | None = None, only: list[int] | None = None
) -> None:
    for admin_id in only if only is not None else get_settings().admin_ids:
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup, disable_web_page_preview=True)
        except TelegramAPIError as e:  # админ не запускал бота или заблокировал его
            log.warning("Failed to notify admin %s: %s", admin_id, e)
