from datetime import datetime
from typing import Any, Optional
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


class DashboardSummaryResponse(BaseModel):
    total_usd: float
    exchanges_count: int
    spot_total: float
    futures_total: float
    dex_total: float
    freshness: str
    plan: dict[str, Any]
    capabilities: dict[str, Any]
    throttling: dict[str, Any]
    integrations: dict[str, int]
    transactions_24h: dict[str, int]
    timestamp: datetime


# ==================== Transaction Schemas ====================


class TransactionSchema(BaseModel):
    """Схема транзакции (ввод/вывод)"""

    id: Optional[int] = None
    tx_id: str  # Уникальный ID транзакции на бирже
    service: str  # Биржа
    tx_type: str  # deposit или withdrawal
    currency: str  # Валюта
    amount: float  # Сумма
    fee: Optional[float] = 0.0  # Комиссия
    fee_currency: Optional[str] = None  # Валюта комиссии
    network: Optional[str] = None  # Сеть (ETH, BSC, TRC20 и т.д.)
    address: Optional[str] = None  # Адрес
    address_from: Optional[str] = None
    address_to: Optional[str] = None
    tag: Optional[str] = None  # Memo/Tag для XRP, EOS и т.д.
    status: str = "pending"  # pending, ok, failed, canceled
    txid: Optional[str] = None  # Хеш транзакции в блокчейне
    tx_timestamp: Optional[datetime] = None  # Время транзакции
    notified: bool = False  # Было ли отправлено уведомление
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TransactionListResponse(BaseModel):
    """Ответ со списком транзакций"""

    service: Optional[str] = None
    tx_type: Optional[str] = None  # deposit, withdrawal или None для всех
    transactions: list[TransactionSchema]
    total_count: int


class TransactionsSummary(BaseModel):
    """Сводка по транзакциям сервиса"""

    service: str
    total_deposits: int
    total_withdrawals: int
    pending_deposits: int
    pending_withdrawals: int
    last_deposit: Optional[datetime] = None
    last_withdrawal: Optional[datetime] = None


class TransactionsRefreshResponse(BaseModel):
    """Ответ на запрос обновления транзакций"""

    status: str
    message: str
    new_transactions: int
    updated_transactions: int
    services_checked: list[str]
    failed_services: list[str]
