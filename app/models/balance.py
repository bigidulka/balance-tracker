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
