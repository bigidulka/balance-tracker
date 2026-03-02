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
    allowed_users: tuple[int, ...]
    hide_small_balance_threshold: float
    notification_interval: int
    min_stablecoin_change: float
    min_token_change_percent: float



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


settings = Settings(
    bot_token=os.getenv("BOT_TOKEN", "").strip(),
    api_url=os.getenv("API_URL", "http://api:8000").rstrip("/"),
    api_token=os.getenv("API_TOKEN", "").strip(),
    api_org_id=os.getenv("API_ORG_ID", "").strip(),
    allowed_users=_parse_int_list(os.getenv("ALLOWED_USERS", "")),
    hide_small_balance_threshold=float(os.getenv("HIDE_SMALL_THRESHOLD", "1.0")),
    notification_interval=int(os.getenv("NOTIFICATION_INTERVAL", "60")),
    min_stablecoin_change=float(os.getenv("MIN_STABLECOIN_CHANGE", "1.0")),
    min_token_change_percent=float(os.getenv("MIN_TOKEN_CHANGE_PERCENT", "0.1")),
)

# Backward-compatible aliases
BOT_TOKEN = settings.bot_token
API_URL = settings.api_url
API_TOKEN = settings.api_token
API_ORG_ID = settings.api_org_id
ALLOWED_USERS = list(settings.allowed_users)
HIDE_SMALL_BALANCE_THRESHOLD = settings.hide_small_balance_threshold
NOTIFICATION_INTERVAL = settings.notification_interval
MIN_STABLECOIN_CHANGE = settings.min_stablecoin_change
MIN_TOKEN_CHANGE_PERCENT = settings.min_token_change_percent
