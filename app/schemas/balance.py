from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class AssetSchema(BaseModel):
    coin: str
    amount: float
    value_usd: float


class AccountBalanceSchema(BaseModel):
    """Баланс одного типа счёта (spot, futures, margin и т.д.)"""

    account_type: str  # spot, futures, margin, swap, unified, funding и т.д.
    assets: list[AssetSchema]
    total_usd: float = Field(default=0.0)


class ServiceBalanceSchema(BaseModel):
    service: str
    accounts: list[AccountBalanceSchema] = Field(default_factory=list)
    assets: list[AssetSchema] = Field(
        default_factory=list
    )  # Deprecated: для совместимости
    total_usd: float = Field(default=0.0)
    updated_at: datetime
    actual: bool = True


class PortfolioResponse(BaseModel):
    total_usd: float
    services: list[ServiceBalanceSchema]
    timestamp: datetime


class RefreshResponse(BaseModel):
    status: str
    message: str
    updated_services: list[str]
    failed_services: list[str]


class HistoryEntrySchema(BaseModel):
    service: str
    total_usd: float
    assets: list[AssetSchema]
    created_at: datetime


class HistoryResponse(BaseModel):
    service: Optional[str] = None
    entries: list[HistoryEntrySchema]
    total_entries: int


class ServiceHealthSchema(BaseModel):
    service: str
    is_healthy: bool
    last_error: Optional[str] = None
    last_check: datetime


class HealthResponse(BaseModel):
    status: str
    services: list[ServiceHealthSchema]
    total_services: int
    healthy_services: int
