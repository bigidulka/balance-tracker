"""Add integration_id to balances, history, and transactions.

Revision ID: 20260321_0008
Revises: 20260321_0007
Create Date: 2026-03-21 12:10:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "20260321_0008"
down_revision: Union[str, None] = "20260321_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _has_column("balances", "integration_id"):
        op.add_column("balances", sa.Column("integration_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_balances_integration_id",
            "balances",
            "integrations",
            ["integration_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if not _has_index("balances", "ix_balances_org_integration_updated"):
        op.create_index(
            "ix_balances_org_integration_updated",
            "balances",
            ["organization_id", "integration_id", "updated_at"],
            unique=False,
        )

    if not _has_column("balance_history", "integration_id"):
        op.add_column("balance_history", sa.Column("integration_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_balance_history_integration_id",
            "balance_history",
            "integrations",
            ["integration_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if not _has_index("balance_history", "ix_balance_history_org_integration_created"):
        op.create_index(
            "ix_balance_history_org_integration_created",
            "balance_history",
            ["organization_id", "integration_id", "created_at"],
            unique=False,
        )

    if not _has_column("transactions", "integration_id"):
        op.add_column("transactions", sa.Column("integration_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_transactions_integration_id",
            "transactions",
            "integrations",
            ["integration_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if not _has_index("transactions", "ix_transactions_org_integration_type"):
        op.create_index(
            "ix_transactions_org_integration_type",
            "transactions",
            ["organization_id", "integration_id", "tx_type"],
            unique=False,
        )
    if _has_index("transactions", "ix_transactions_org_service_tx_id"):
        op.drop_index("ix_transactions_org_service_tx_id", table_name="transactions")
    op.create_index(
        "ix_transactions_org_service_tx_id",
        "transactions",
        ["organization_id", "service", "tx_id"],
        unique=False,
    )
    if not _has_index("transactions", "ix_transactions_org_integration_tx_id"):
        op.create_index(
            "ix_transactions_org_integration_tx_id",
            "transactions",
            ["organization_id", "integration_id", "tx_id"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    balance_indexes = {row["name"] for row in inspector.get_indexes("balances")}
    if "ix_balances_org_integration_updated" in balance_indexes:
        op.drop_index("ix_balances_org_integration_updated", table_name="balances")
    if _has_column("balances", "integration_id"):
        op.drop_constraint("fk_balances_integration_id", "balances", type_="foreignkey")
        op.drop_column("balances", "integration_id")

    history_indexes = {row["name"] for row in inspector.get_indexes("balance_history")}
    if "ix_balance_history_org_integration_created" in history_indexes:
        op.drop_index("ix_balance_history_org_integration_created", table_name="balance_history")
    if _has_column("balance_history", "integration_id"):
        op.drop_constraint("fk_balance_history_integration_id", "balance_history", type_="foreignkey")
        op.drop_column("balance_history", "integration_id")

    tx_indexes = {row["name"] for row in inspector.get_indexes("transactions")}
    if "ix_transactions_org_integration_tx_id" in tx_indexes:
        op.drop_index("ix_transactions_org_integration_tx_id", table_name="transactions")
    if "ix_transactions_org_integration_type" in tx_indexes:
        op.drop_index("ix_transactions_org_integration_type", table_name="transactions")
    if "ix_transactions_org_service_tx_id" in tx_indexes:
        op.drop_index("ix_transactions_org_service_tx_id", table_name="transactions")
        op.create_index(
            "ix_transactions_org_service_tx_id",
            "transactions",
            ["organization_id", "service", "tx_id"],
            unique=True,
        )
    if _has_column("transactions", "integration_id"):
        op.drop_constraint("fk_transactions_integration_id", "transactions", type_="foreignkey")
        op.drop_column("transactions", "integration_id")
