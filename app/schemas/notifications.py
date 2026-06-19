from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class NotificationSettingsUpdate(BaseModel):
    enabled: bool | None = None
    system_enabled: bool | None = None
    transaction_enabled: bool | None = None
    balance_enabled: bool | None = None
    min_balance_delta_usd: float | None = Field(default=None, ge=0)
    min_balance_delta_percent: float | None = Field(default=None, ge=0)
    muted_services: list[str] | None = None
    channels: list[str] | None = None


class NotificationSettingsResponse(BaseModel):
    id: int
    organization_id: int
    user_id: int
    enabled: bool
    system_enabled: bool
    transaction_enabled: bool
    balance_enabled: bool
    min_balance_delta_usd: float
    min_balance_delta_percent: float
    muted_services: list[str]
    channels: list[str]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NotificationEventCreate(BaseModel):
    organization_id: int
    user_id: int
    event_type: str
    title: str
    body: str
    severity: str = "info"
    source: str = "system"
    integration_id: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str
    send_after_at: datetime | None = None


class NotificationEventResponse(BaseModel):
    id: int
    organization_id: int
    user_id: int
    integration_id: int | None = None
    event_type: str
    severity: str
    source: str
    title: str
    body: str
    payload: dict[str, Any]
    dedupe_key: str
    status: str
    error_message: str | None = None
    send_after_at: datetime | None = None
    sent_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NotificationEventListResponse(BaseModel):
    events: list[NotificationEventResponse]
    total_count: int
