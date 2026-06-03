from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware

from app.services.backend_api import BackendApi
from app.services.navigation import NavigationService


class ServicesMiddleware(BaseMiddleware):
    def __init__(self, backend_api: BackendApi, navigation: NavigationService) -> None:
        self._backend_api = backend_api
        self._navigation = navigation

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        data["backend_api"] = self._backend_api
        data["navigation"] = self._navigation
        return await handler(event, data)
