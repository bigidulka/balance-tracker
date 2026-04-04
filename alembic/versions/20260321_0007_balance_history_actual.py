"""Add actual column to balance_history.

Revision ID: 20260321_0007
Revises: 20260320_0006
Create Date: 2026-03-21 10:30:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "20260321_0007"
down_revision: Union[str, None] = "20260320_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_column("balance_history", "actual"):
        op.add_column(
            "balance_history",
            sa.Column("actual", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    if _has_column("balance_history", "actual"):
        op.drop_column("balance_history", "actual")
