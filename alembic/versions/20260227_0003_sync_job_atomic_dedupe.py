"""Add sync_jobs dedupe key and active unique index.

Revision ID: 20260227_0003
Revises: 20260226_0002
Create Date: 2026-02-27 00:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "20260227_0003"
down_revision: Union[str, None] = "20260226_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = inspector.get_columns(table_name)
    return any(column.get("name") == column_name for column in columns)


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_indexes = inspector.get_indexes(table_name)
    return any(index.get("name") == index_name for index in existing_indexes)


def upgrade() -> None:
    if not _has_column("sync_jobs", "dedupe_key"):
        op.add_column("sync_jobs", sa.Column("dedupe_key", sa.String(length=128), nullable=True))

    if not _has_index("sync_jobs", "ix_sync_jobs_dedupe_key"):
        op.create_index("ix_sync_jobs_dedupe_key", "sync_jobs", ["dedupe_key"], unique=False)

    if not _has_index("sync_jobs", "uq_sync_jobs_active_dedupe_key"):
        op.create_index(
            "uq_sync_jobs_active_dedupe_key",
            "sync_jobs",
            ["dedupe_key"],
            unique=True,
            postgresql_where=sa.text("status IN ('queued','running') AND dedupe_key IS NOT NULL"),
            sqlite_where=sa.text("status IN ('queued','running') AND dedupe_key IS NOT NULL"),
        )


def downgrade() -> None:
    if _has_index("sync_jobs", "uq_sync_jobs_active_dedupe_key"):
        op.drop_index("uq_sync_jobs_active_dedupe_key", table_name="sync_jobs")

    if _has_index("sync_jobs", "ix_sync_jobs_dedupe_key"):
        op.drop_index("ix_sync_jobs_dedupe_key", table_name="sync_jobs")

    if _has_column("sync_jobs", "dedupe_key"):
        op.drop_column("sync_jobs", "dedupe_key")
