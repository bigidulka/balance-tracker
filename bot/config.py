"""Bot configuration loaded from environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    bot_token: str
    api_url: str
    api_token: str
    api_org_id: str
    redis_url: str
    allowed_users: tuple[int, ...]
    admin_users: tuple[int, ...]
    hide_small_balance_threshold: float
    notification_interval: int
    enable_notification_loop: bool
    min_stablecoin_change: float
    min_token_change_percent: float
    no_backend_ui_mode: bool
    debank_sdk_url: str



def _parse_int_list(raw: str) -> tuple[int, ...]:
    values: list[int] = []
    for chunk in raw.split(","):
        value = chunk.strip()
        if not value:
            continue
        try:
            values.append(int(value))
        except ValueError:
            continue
    return tuple(values)



def _parse_bool(value: str, default: bool = False) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "on", "y"}


settings = Settings(
    bot_token=os.getenv("BOT_TOKEN", "").strip(),
    api_url=os.getenv("API_URL", "http://api:8000").rstrip("/"),
    api_token=os.getenv("API_TOKEN", "").strip(),
    api_org_id=os.getenv("API_ORG_ID", "").strip(),
    redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0").strip(),
    allowed_users=_parse_int_list(os.getenv("ALLOWED_USERS", "")),
    admin_users=_parse_int_list(os.getenv("BOT_ADMIN_USERS", "6238100241")),
    hide_small_balance_threshold=float(os.getenv("HIDE_SMALL_THRESHOLD", "1.0")),
    notification_interval=int(os.getenv("NOTIFICATION_INTERVAL", "60")),
    enable_notification_loop=_parse_bool(os.getenv("ENABLE_NOTIFICATION_LOOP", "false"), default=False),
    min_stablecoin_change=float(os.getenv("MIN_STABLECOIN_CHANGE", "1.0")),
    min_token_change_percent=float(os.getenv("MIN_TOKEN_CHANGE_PERCENT", "0.1")),
    no_backend_ui_mode=_parse_bool(os.getenv("NO_BACKEND_UI_MODE", "false"), default=False),
    debank_sdk_url=os.getenv("DEBANK_SDK_URL", "http://debank-sdk:8080").rstrip("/"),
)

# Backward-compatible aliases
BOT_TOKEN = settings.bot_token
API_URL = settings.api_url
API_TOKEN = settings.api_token
API_ORG_ID = settings.api_org_id
REDIS_URL = settings.redis_url
ALLOWED_USERS = list(settings.allowed_users)
BOT_ADMIN_USERS = list(settings.admin_users)
HIDE_SMALL_BALANCE_THRESHOLD = settings.hide_small_balance_threshold
NOTIFICATION_INTERVAL = settings.notification_interval
ENABLE_NOTIFICATION_LOOP = settings.enable_notification_loop
MIN_STABLECOIN_CHANGE = settings.min_stablecoin_change
MIN_TOKEN_CHANGE_PERCENT = settings.min_token_change_percent
NO_BACKEND_UI_MODE = settings.no_backend_ui_mode
