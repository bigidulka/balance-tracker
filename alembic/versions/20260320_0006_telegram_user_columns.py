"""Add Telegram fields to users for platform admin checks.

Revision ID: 20260320_0006
Revises: 20260320_0005
Create Date: 2026-03-20 13:15:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "20260320_0006"
down_revision: Union[str, None] = "20260320_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_column("users", "telegram_user_id"):
        op.add_column("users", sa.Column("telegram_user_id", sa.BigInteger(), nullable=True))
    if not _has_column("users", "telegram_username"):
        op.add_column("users", sa.Column("telegram_username", sa.String(length=255), nullable=True))
    if not _has_column("users", "telegram_full_name"):
        op.add_column("users", sa.Column("telegram_full_name", sa.String(length=255), nullable=True))

    bind = op.get_bind()
    inspector = inspect(bind)
    user_indexes = {row["name"] for row in inspector.get_indexes("users")}
    if "ix_users_telegram_user_id" not in user_indexes:
        op.create_index("ix_users_telegram_user_id", "users", ["telegram_user_id"], unique=True)
    if "ix_users_telegram_username" not in user_indexes:
        op.create_index("ix_users_telegram_username", "users", ["telegram_username"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    user_indexes = {row["name"] for row in inspector.get_indexes("users")}
    if "ix_users_telegram_username" in user_indexes:
        op.drop_index("ix_users_telegram_username", table_name="users")
    if "ix_users_telegram_user_id" in user_indexes:
        op.drop_index("ix_users_telegram_user_id", table_name="users")

    columns = {column["name"] for column in inspector.get_columns("users")}
    if "telegram_full_name" in columns:
        op.drop_column("users", "telegram_full_name")
    if "telegram_username" in columns:
        op.drop_column("users", "telegram_username")
    if "telegram_user_id" in columns:
        op.drop_column("users", "telegram_user_id")
