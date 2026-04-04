"""Middleware that injects repositories and services into handlers."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.repositories.api_repository import ApiRepository
from bot.repositories.user_repository import UserRepository
from bot.services.navigation_service import NavigationService
from bot.services.screen_service import ScreenService
from bot.services.runtime import (
    ensure_backend_auth_session,
    set_current_backend_auth_session,
    set_current_telegram_user_id,
)
from bot.state.repository import UiStateRepository


class ContextMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self._api_repo = ApiRepository()
        self._user_repo = UserRepository()
        self._state_repo = UiStateRepository()
        self._navigation_service = NavigationService(self._state_repo)
        self._screen_service = ScreenService(self._api_repo, self._user_repo)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        telegram_user = getattr(event, "from_user", None)
        telegram_user_id = getattr(telegram_user, "id", None)
        telegram_user_id_token = None
        backend_auth_token = None
        try:
            if isinstance(telegram_user_id, int):
                telegram_user_id_token = set_current_telegram_user_id(telegram_user_id)
                try:
                    backend_auth = await ensure_backend_auth_session(
                        telegram_user_id=telegram_user_id,
                        username=getattr(telegram_user, "username", None),
                        full_name=getattr(telegram_user, "full_name", None),
                    )
                except Exception as exc:
                    data["backend_auth_error"] = str(exc)
                else:
                    backend_auth_token = set_current_backend_auth_session(backend_auth)
                    data["backend_auth"] = backend_auth
                data["telegram_user_id"] = telegram_user_id

            data["api_repo"] = self._api_repo
            data["user_repo"] = self._user_repo
            data["ui_state_repo"] = self._state_repo
            data["navigation_service"] = self._navigation_service
            data["nav_service"] = self._navigation_service
            data["screen_service"] = self._screen_service
            return await handler(event, data)
        finally:
            if telegram_user_id_token is not None:
                telegram_user_id_token.var.reset(telegram_user_id_token)
            if backend_auth_token is not None:
                backend_auth_token.var.reset(backend_auth_token)
