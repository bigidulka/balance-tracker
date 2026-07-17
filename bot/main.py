import asyncio
import logging
import os
from urllib.parse import urlsplit
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from bot.config import settings

OUTBOUND_PROXY_URL = os.getenv("OUTBOUND_PROXY_URL", "").strip()
from bot.handlers import router
from bot.api_client import api_client
from bot.middlewares.context import ContextMiddleware
from bot.notifications import notification_event_loop, notification_loop, transaction_notification_loop
from bot.services.runtime import close_runtime

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Background tasks for notifications
_notification_task: asyncio.Task | None = None
_tx_notification_task: asyncio.Task | None = None


async def _build_storage():
    try:
        redis = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        await redis.ping()
        logger.info("Using Redis FSM storage at %s", settings.redis_url)
        return RedisStorage(redis=redis)
    except Exception as exc:
        logger.warning("Redis FSM storage unavailable, falling back to MemoryStorage: %s", exc)
        return MemoryStorage()


async def main():
    global _notification_task, _tx_notification_task

    if not settings.bot_token:
        logger.error("BOT_TOKEN is not set")
        return

    session: AiohttpSession | None = None
    if OUTBOUND_PROXY_URL:
        parsed_proxy = urlsplit(OUTBOUND_PROXY_URL)
        proxy_label = parsed_proxy.hostname or "configured"
        if parsed_proxy.port:
            proxy_label = f"{proxy_label}:{parsed_proxy.port}"
        logger.info("Using proxy for bot: %s", proxy_label)
        session = AiohttpSession(proxy=OUTBOUND_PROXY_URL)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )
    storage = await _build_storage()
    dp = Dispatcher(storage=storage)
    context_middleware = ContextMiddleware()
    dp.message.middleware(context_middleware)
    dp.callback_query.middleware(context_middleware)
    dp.include_router(router)

    logger.info("Starting bot...")

    if settings.enable_notification_loop:
        _notification_task = asyncio.create_task(
            notification_event_loop(bot, interval=settings.notification_interval)
        )
        logger.info(
            "Notification event loop started (interval: %ss)",
            settings.notification_interval,
        )

    # Legacy balance-delta notifications remain disabled; event outbox loop above is used.

    # Start transaction notification background task (check every 2 minutes)
    # _tx_notification_task = asyncio.create_task(
    #     transaction_notification_loop(bot, interval=tx_interval)
    # )
    # logger.info(f"Transaction notification loop started (interval: {tx_interval}s)")

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
        await close_runtime()
        await storage.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
