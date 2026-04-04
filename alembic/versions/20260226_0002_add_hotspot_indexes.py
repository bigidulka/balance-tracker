"""Add practical hotspot indexes for history, queue, and notifications.

Revision ID: 20260226_0002
Revises: 20260226_0001
Create Date: 2026-02-26 00:10:00
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "20260226_0002"
down_revision: Union[str, None] = "20260226_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_indexes = inspector.get_indexes(table_name)
    return any(index.get("name") == index_name for index in existing_indexes)


def upgrade() -> None:
    if not _has_index("balance_history", "ix_balance_history_org_created"):
        op.create_index(
            "ix_balance_history_org_created",
            "balance_history",
            ["organization_id", "created_at"],
            unique=False,
        )

    if not _has_index(
        "transactions", "ix_transactions_org_status_notified_timestamp"
    ):
        op.create_index(
            "ix_transactions_org_status_notified_timestamp",
            "transactions",
            ["organization_id", "status", "notified", "tx_timestamp"],
            unique=False,
        )

    if not _has_index("sync_jobs", "ix_sync_jobs_status_queued_at"):
        op.create_index(
            "ix_sync_jobs_status_queued_at",
            "sync_jobs",
            ["status", "queued_at", "id"],
            unique=False,
        )


def downgrade() -> None:
    if _has_index("sync_jobs", "ix_sync_jobs_status_queued_at"):
        op.drop_index("ix_sync_jobs_status_queued_at", table_name="sync_jobs")

    if _has_index("transactions", "ix_transactions_org_status_notified_timestamp"):
        op.drop_index(
            "ix_transactions_org_status_notified_timestamp", table_name="transactions"
        )

    if _has_index("balance_history", "ix_balance_history_org_created"):
        op.drop_index("ix_balance_history_org_created", table_name="balance_history")
