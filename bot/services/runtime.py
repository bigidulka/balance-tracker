"""In-memory runtime state for the bot."""

from __future__ import annotations

from typing import Any

from bot.config import settings

_user_settings: dict[int, dict[str, Any]] = {}
_subscribers: set[int] = set()


def get_user_settings(user_id: int) -> dict[str, Any]:
    if user_id not in _user_settings:
        _user_settings[user_id] = {
            "hide_small": False,
            "tx_type": "all",
            "tx_status": "all",
            "tx_service": "all",
            "tx_since_hours": 24,
        }
    return _user_settings[user_id]


def register_subscriber(user_id: int) -> None:
    _subscribers.add(user_id)


def get_notification_recipients() -> list[int]:
    if _subscribers:
        return sorted(_subscribers)
    return list(settings.allowed_users)


def is_local_user_allowed(user_id: int) -> bool:
    """
    SaaS auth/token is primary gate.
    Local allowlist is only fallback when SaaS auth is not configured.
    """
    if settings.api_token and settings.api_org_id:
        return True
    if not settings.allowed_users:
        return True
    return user_id in settings.allowed_users
