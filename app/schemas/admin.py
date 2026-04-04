from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AdminUserListItem(BaseModel):
    membership_id: int
    organization_id: int
    organization_name: str
    user_id: int
    email: str
    full_name: str | None = None
    telegram_user_id: int | None = None
    telegram_username: str | None = None
    telegram_full_name: str | None = None
    role: str
    is_active: bool
    balance_usd: float = 0.0
    integrations_total: int = 0
    active_integrations: int = 0
    plan_code: str
    plan_name: str
    created_at: datetime | None = None


class AdminUsersPageResponse(BaseModel):
    items: list[AdminUserListItem] = Field(default_factory=list)
    total: int = 0
    limit: int = 20
    offset: int = 0


class AdminIntegrationItem(BaseModel):
    id: int
    name: str
    provider: str
    kind: str
    exchange_code: str | None = None
    wallet_address: str | None = None
    chain: str | None = None
    is_active: bool


class AdminUserDetailResponse(AdminUserListItem):
    integrations: list[AdminIntegrationItem] = Field(default_factory=list)
