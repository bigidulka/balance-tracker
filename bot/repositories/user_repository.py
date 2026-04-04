"""Repository wrapper for user runtime settings/state."""

from __future__ import annotations

from typing import Any

from bot.services.runtime import (
    get_user_settings,
    is_local_user_admin,
    is_local_user_allowed,
    register_subscriber,
    save_user_settings,
)


class UserRepository:
    async def get_settings(self, user_id: int) -> dict[str, Any]:
        return await get_user_settings(user_id)

    async def save_settings(self, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return await save_user_settings(user_id, payload)

    async def register_subscriber(self, user_id: int) -> None:
        await register_subscriber(user_id)

    async def is_local_allowed(self, user_id: int) -> bool:
        return await is_local_user_allowed(user_id)

    async def is_local_admin(self, user_id: int) -> bool:
        return await is_local_user_admin(user_id)
