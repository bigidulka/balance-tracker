import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode

from bot.config import BOT_TOKEN, NOTIFICATION_INTERVAL
from bot.handlers import router
from bot.api_client import api_client
from bot.notifications import notification_loop, transaction_notification_loop

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Background tasks for notifications
_notification_task: asyncio.Task | None = None
_tx_notification_task: asyncio.Task | None = None


async def main():
    global _notification_task, _tx_notification_task

    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set")
        return

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Starting bot...")

    # Start notification background tasks
    _notification_task = asyncio.create_task(
        notification_loop(bot, interval=NOTIFICATION_INTERVAL)
    )
    logger.info(f"Balance notification loop started (interval: {NOTIFICATION_INTERVAL}s)")

    # Start transaction notification background task (check every 2 minutes)
    tx_interval = max(NOTIFICATION_INTERVAL, 120)
    _tx_notification_task = asyncio.create_task(
        transaction_notification_loop(bot, interval=tx_interval)
    )
    logger.info(f"Transaction notification loop started (interval: {tx_interval}s)")

    try:
        await dp.start_polling(bot)
    finally:
        # Cancel notification tasks
        if _notification_task:
            _notification_task.cancel()
            try:
                await _notification_task
            except asyncio.CancelledError:
                pass

        if _tx_notification_task:
            _tx_notification_task.cancel()
            try:
                await _tx_notification_task
            except asyncio.CancelledError:
                pass

        await api_client.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
