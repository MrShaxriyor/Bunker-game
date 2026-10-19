"""Safe startup helpers for the Aiogram polling process."""

import logging

from aiogram.exceptions import TelegramAPIError, TelegramConflictError

logger = logging.getLogger(__name__)


async def verify_bot_identity(bot):
    """Verify the configured token and log only Telegram's public bot identity."""
    try:
        bot_info = await bot.get_me()
    except TelegramAPIError as error:
        logger.error(
            "Telegram bot identity check failed: error_type=%s",
            type(error).__name__,
        )
        raise SystemExit(1) from None
    username = f"@{bot_info.username}" if bot_info.username else "(username unavailable)"
    logger.info("Telegram bot identity: username=%s id=%s", username, bot_info.id)
    return bot_info


async def start_polling(dispatcher, bot) -> None:
    """Run polling once and report getUpdates conflicts without retrying."""
    try:
        await dispatcher.start_polling(bot)
    except TelegramConflictError:
        logger.error(
            "Telegram polling conflict: another process is already calling getUpdates "
            "for this bot. Stop the duplicate local bot process and try again."
        )
        raise SystemExit(1) from None
