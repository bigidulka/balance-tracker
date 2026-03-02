"""
DbSession Middleware
Инъекция сессии БД в контекст обработчика
"""

import logging
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


class DbSessionMiddleware(BaseMiddleware):
    """
    Middleware для инъекции сессии БД

    Использование:
        dp.update.middleware(DbSessionMiddleware(session_maker))

    В хендлере:
        async def handler(message: Message, session: AsyncSession):
            ...
    """

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]):
        self.session_maker = session_maker

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        async with self.session_maker() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
