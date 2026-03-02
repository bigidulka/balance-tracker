"""
Config middleware
Инъекция конфигурации в handlers через data["config"]
"""

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from config import Settings


class ConfigMiddleware(BaseMiddleware):
    """Middleware для передачи конфига в обработчики"""

    def __init__(self, config: Settings):
        self.config = config

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        data["config"] = self.config
        return await handler(event, data)

    async def close(self) -> None:
        """Единая точка закрытия ресурсов (на будущее)"""
        return None
