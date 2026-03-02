from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Boolean,
    JSON,
    Index,
    ForeignKey,
    UniqueConstraint,
    Text,
)
from sqlalchemy.sql import func
from app.core.database import Base


DEFAULT_ORGANIZATION_ID = 1


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False)
    slug = Column(String(120), nullable=False, unique=True, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OrganizationMembership(Base):
    __tablename__ = "organization_memberships"

    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(20), nullable=False, default="member")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_org_membership_unique", "organization_id", "user_id", unique=True),)


class Balance(Base):
    __tablename__ = "balances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, nullable=False, default=DEFAULT_ORGANIZATION_ID, index=True)
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

    __table_args__ = (
        Index("ix_balances_org_service_updated", "organization_id", "service", "updated_at"),
    )


class BalanceHistory(Base):
    __tablename__ = "balance_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, nullable=False, default=DEFAULT_ORGANIZATION_ID, index=True)
    service = Column(String(50), nullable=False, index=True)
    assets = Column(JSON, nullable=False, default=list)  # Deprecated
    accounts = Column(
        JSON, nullable=False, default=list
    )  # List of {account_type, assets, total_usd}
    total_usd = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index(
            "ix_balance_history_org_service_created",
            "organization_id",
            "service",
            "created_at",
        ),
        Index("ix_balance_history_org_created", "organization_id", "created_at"),
    )


class ServiceStatus(Base):
    __tablename__ = "service_status"

    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, nullable=False, default=DEFAULT_ORGANIZATION_ID, index=True)
    service = Column(String(50), nullable=False, index=True)
    is_healthy = Column(Boolean, nullable=False, default=True)
    last_error = Column(String(500), nullable=True)
    last_check = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_service_status_org_service", "organization_id", "service", unique=True),
    )


class Transaction(Base):
    """
    Модель для хранения транзакций вводов/выводов.
    Хранит уникальные транзакции по tx_id и service.
    """

    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, nullable=False, default=DEFAULT_ORGANIZATION_ID, index=True)

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
        Index("ix_transactions_org_service_type", "organization_id", "service", "tx_type"),
        Index(
            "ix_transactions_org_service_tx_id",
            "organization_id",
            "service",
            "tx_id",
            unique=True,
        ),
        Index("ix_transactions_org_status_notified", "organization_id", "status", "notified"),
        Index("ix_transactions_org_tx_timestamp", "organization_id", "tx_timestamp"),
        Index(
            "ix_transactions_org_status_notified_timestamp",
            "organization_id",
            "status",
            "notified",
            "tx_timestamp",
        ),
    )


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(50), nullable=False, unique=True, index=True)
    name = Column(String(120), nullable=False)
    max_integrations = Column(Integer, nullable=False, default=3)
    min_refresh_interval_seconds = Column(Integer, nullable=False, default=300)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OrganizationSubscription(Base):
    __tablename__ = "organization_subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, nullable=False, index=True)
    plan_code = Column(String(50), nullable=False, index=True)
    status = Column(String(50), nullable=False, default="active")
    current_period_end = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_org_subscriptions_org_status", "organization_id", "status"),
    )


class Integration(Base):
    __tablename__ = "integrations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider = Column(String(50), nullable=False, index=True)
    kind = Column(String(10), nullable=False, default="cex", index=True)
    exchange_code = Column(String(50), nullable=True, index=True)
    account_ref = Column(String(255), nullable=True)
    wallet_address = Column(String(255), nullable=True)
    chain = Column(String(50), nullable=True, index=True)
    external_id = Column(String(255), nullable=True)
    name = Column(String(120), nullable=False)
    status = Column(String(20), nullable=False, default="active")
    is_active = Column(Boolean, nullable=False, default=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_integrations_org_active", "organization_id", "is_active"),
        Index("ix_integrations_org_provider", "organization_id", "provider"),
        Index("ix_integrations_org_kind_exchange", "organization_id", "kind", "exchange_code"),
        UniqueConstraint(
            "organization_id",
            "kind",
            "exchange_code",
            "account_ref",
            "is_active",
            name="uq_integrations_org_cex_account_active",
        ),
        UniqueConstraint(
            "organization_id",
            "kind",
            "chain",
            "wallet_address",
            "is_active",
            name="uq_integrations_org_dex_wallet_active",
        ),
    )


class IntegrationSecret(Base):
    __tablename__ = "integration_secrets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    integration_id = Column(
        Integer,
        ForeignKey("integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key_name = Column(String(100), nullable=False)
    secret_value = Column(Text, nullable=False)
    is_encrypted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("integration_id", "key_name", name="uq_integration_secret_key"),
        Index("ix_integration_secrets_integration_key", "integration_id", "key_name"),
    )


class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    integration_id = Column(
        Integer,
        ForeignKey("integrations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    job_type = Column(String(50), nullable=False, default="refresh")
    status = Column(String(20), nullable=False, default="queued")
    dedupe_key = Column(String(128), nullable=True)
    payload = Column(JSON, nullable=False, default=dict)
    result = Column(JSON, nullable=False, default=dict)
    error_message = Column(String(1000), nullable=True)
    queued_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_sync_jobs_org_status", "organization_id", "status"),
        Index("ix_sync_jobs_org_integration", "organization_id", "integration_id"),
        Index("ix_sync_jobs_status_queued_at", "status", "queued_at", "id"),
        Index("ix_sync_jobs_dedupe_key", "dedupe_key"),
        Index(
            "uq_sync_jobs_active_dedupe_key",
            "dedupe_key",
            unique=True,
            postgresql_where=(status.in_(["queued", "running"]) & dedupe_key.isnot(None)),
            sqlite_where=(status.in_(["queued", "running"]) & dedupe_key.isnot(None)),
        ),
    )


class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(50), nullable=False, unique=True, index=True)
    name = Column(String(120), nullable=False)
    max_integrations = Column(Integer, nullable=False, default=3)
    min_refresh_interval_seconds = Column(Integer, nullable=False, default=300)
    policy_json = Column(JSON, nullable=False, default=dict)
    price_monthly = Column(Float, nullable=False, default=0.0)
    currency = Column(String(10), nullable=False, default="USD")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_id = Column(
        Integer,
        ForeignKey("plans.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status = Column(String(50), nullable=False, default="active")
    current_period_end = Column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_subscriptions_org_status", "organization_id", "status"),
    )


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    metric = Column(String(100), nullable=False, index=True)
    quantity = Column(Float, nullable=False, default=1.0)
    period = Column(String(20), nullable=False, default="monthly")
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_usage_events_org_metric", "organization_id", "metric"),
    )


class BillingEvent(Base):
    __tablename__ = "billing_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type = Column(String(100), nullable=False, index=True)
    provider = Column(String(50), nullable=True)
    provider_event_id = Column(String(255), nullable=True)
    amount = Column(Float, nullable=False, default=0.0)
    currency = Column(String(10), nullable=False, default="USD")
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index(
            "ix_billing_events_org_provider_event",
            "organization_id",
            "provider",
            "provider_event_id",
        ),
    )


class BillingWebhookEvent(Base):
    __tablename__ = "billing_webhook_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(50), nullable=False)
    external_event_id = Column(String(255), nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    processed_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("provider", "external_event_id", name="uq_billing_event_provider_external_id"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    action = Column(String(120), nullable=False, index=True)
    resource_type = Column(String(80), nullable=False)
    resource_id = Column(String(120), nullable=True)
    details = Column(JSON, nullable=False, default=dict)
    request_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_audit_logs_org_action_created", "organization_id", "action", "created_at"),
    )
