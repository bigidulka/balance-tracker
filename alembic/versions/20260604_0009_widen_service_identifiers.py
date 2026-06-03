"""Widen service identifier columns for wallet service keys.

Revision ID: 20260604_0009
Revises: 20260321_0008
Create Date: 2026-06-04 00:30:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260604_0009"
down_revision: Union[str, None] = "20260321_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SERVICE_TABLES = ("balances", "balance_history", "service_status", "transactions")


def upgrade() -> None:
    for table_name in SERVICE_TABLES:
        op.alter_column(
            table_name,
            "service",
            existing_type=sa.String(length=50),
            type_=sa.String(length=128),
            existing_nullable=False,
        )


def downgrade() -> None:
    for table_name in SERVICE_TABLES:
        op.alter_column(
            table_name,
            "service",
            existing_type=sa.String(length=128),
            type_=sa.String(length=50),
            existing_nullable=False,
        )
