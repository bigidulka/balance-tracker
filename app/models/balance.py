from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, JSON, Index
from sqlalchemy.sql import func
from app.core.database import Base


class Balance(Base):
    __tablename__ = "balances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    service = Column(String(50), nullable=False, index=True)
    assets = Column(JSON, nullable=False, default=list)  # Deprecated: for compatibility
    accounts = Column(
        JSON, nullable=False, default=list
    )  # List of {account_type, assets, total_usd}
    total_usd = Column(Float, nullable=False, default=0.0)
    actual = Column(Boolean, nullable=False, default=True)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_balances_service_updated", "service", "updated_at"),)


class BalanceHistory(Base):
    __tablename__ = "balance_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    service = Column(String(50), nullable=False, index=True)
    assets = Column(JSON, nullable=False, default=list)  # Deprecated
    accounts = Column(
        JSON, nullable=False, default=list
    )  # List of {account_type, assets, total_usd}
    total_usd = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_balance_history_service_created", "service", "created_at"),
    )


class ServiceStatus(Base):
    __tablename__ = "service_status"

    id = Column(Integer, primary_key=True, autoincrement=True)
    service = Column(String(50), nullable=False, unique=True, index=True)
    is_healthy = Column(Boolean, nullable=False, default=True)
    last_error = Column(String(500), nullable=True)
    last_check = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Transaction(Base):
    """
    Модель для хранения транзакций вводов/выводов.
    Хранит уникальные транзакции по tx_id и service.
    """

    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Уникальный идентификатор транзакции на бирже
    tx_id = Column(String(255), nullable=False, index=True)

    # Сервис (биржа)
    service = Column(String(50), nullable=False, index=True)

    # Тип транзакции: deposit или withdrawal
    tx_type = Column(String(20), nullable=False, index=True)

    # Валюта
    currency = Column(String(50), nullable=False)

    # Сумма транзакции
    amount = Column(Float, nullable=False)

    # Комиссия (если есть)
    fee = Column(Float, nullable=True, default=0.0)
    fee_currency = Column(String(50), nullable=True)

    # Сеть (ETH, BSC, TRC20, etc.)
    network = Column(String(50), nullable=True)

    # Адреса
    address = Column(String(255), nullable=True)
    address_from = Column(String(255), nullable=True)
    address_to = Column(String(255), nullable=True)

    # Тег/Memo (для XRP, EOS, etc.)
    tag = Column(String(100), nullable=True)

    # Статус транзакции: pending, ok, failed, canceled
    status = Column(String(50), nullable=False, default="pending")

    # Хеш транзакции в блокчейне
    txid = Column(String(255), nullable=True)

    # Время транзакции на бирже
    tx_timestamp = Column(DateTime(timezone=True), nullable=True)

    # Флаг отправки уведомления
    notified = Column(Boolean, nullable=False, default=False)

    # Время создания/обновления в БД
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_transactions_service_type", "service", "tx_type"),
        Index("ix_transactions_service_tx_id", "service", "tx_id", unique=True),
        Index("ix_transactions_status_notified", "status", "notified"),
        Index("ix_transactions_tx_timestamp", "tx_timestamp"),
    )
