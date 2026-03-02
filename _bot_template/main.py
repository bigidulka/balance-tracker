"""
Точка входа бота
Инициализация и запуск aiogram диспетчера
"""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import RedisStorage
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from _core.database.models import Base

from config import settings
from handlers import router
from middlewares import DbSessionMiddleware, ConfigMiddleware

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


async def on_startup(bot: Bot) -> None:
    """Действия при запуске бота"""
    me = await bot.get_me()
    logger.info(f"Бот запущен: @{me.username}")


async def on_shutdown(bot: Bot) -> None:
    """Действия при остановке бота"""
    logger.info("Бот остановлен")


async def main() -> None:
    """Главная функция запуска"""
    logger.info("Инициализация бота...")

    # Подключение к БД
    engine = create_async_engine(
        settings.database_url,
        echo=settings.log_level == "DEBUG",
        pool_size=5,
        max_overflow=10,
    )

    # Создание таблиц (если нужно)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Фабрика сессий
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    # Redis FSM
    storage = RedisStorage.from_url(settings.redis_fsm_url)

    # Инициализация бота
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Инициализация диспетчера
    dp = Dispatcher(storage=storage)

    config_middleware = ConfigMiddleware(settings)

    # Регистрация middleware
    dp.message.middleware(DbSessionMiddleware(session_maker))
    dp.message.middleware(config_middleware)
    dp.callback_query.middleware(DbSessionMiddleware(session_maker))
    dp.callback_query.middleware(config_middleware)

    # Регистрация роутеров
    dp.include_router(router)

    # Callback-и жизненного цикла
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Запуск polling
    logger.info("Запуск polling...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await storage.close()
        await config_middleware.close()
        await engine.dispose()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен вручную")
