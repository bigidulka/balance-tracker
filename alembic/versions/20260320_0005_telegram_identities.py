"""Add telegram identities for per-user bot tenancy.

Revision ID: 20260320_0005
Revises: 20260320_0004
Create Date: 2026-03-20 13:00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "20260320_0005"
down_revision: Union[str, None] = "20260320_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("telegram_identities"):
        op.create_table(
            "telegram_identities",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
            sa.Column("telegram_username", sa.String(length=255), nullable=True),
            sa.Column("telegram_first_name", sa.String(length=255), nullable=True),
            sa.Column("telegram_last_name", sa.String(length=255), nullable=True),
            sa.Column("telegram_full_name", sa.String(length=255), nullable=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("telegram_user_id", name="uq_telegram_identities_telegram_user_id"),
            sa.UniqueConstraint("user_id", name="uq_telegram_identities_user_id"),
            sa.UniqueConstraint("organization_id", name="uq_telegram_identities_organization_id"),
        )
        op.create_index(
            "ix_telegram_identities_telegram_user_id",
            "telegram_identities",
            ["telegram_user_id"],
            unique=True,
        )
        op.create_index("ix_telegram_identities_user_id", "telegram_identities", ["user_id"], unique=True)
        op.create_index(
            "ix_telegram_identities_organization_id",
            "telegram_identities",
            ["organization_id"],
            unique=True,
        )


def downgrade() -> None:
    if _has_table("telegram_identities"):
        op.drop_index("ix_telegram_identities_organization_id", table_name="telegram_identities")
        op.drop_index("ix_telegram_identities_user_id", table_name="telegram_identities")
        op.drop_index("ix_telegram_identities_telegram_user_id", table_name="telegram_identities")
        op.drop_table("telegram_identities")
