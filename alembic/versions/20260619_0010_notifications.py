"""Add notification settings, cursors, and events.

Revision ID: 20260619_0010
Revises: 20260604_0009
Create Date: 2026-06-19 01:35:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260619_0010"
down_revision: Union[str, None] = "20260604_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("system_enabled", sa.Boolean(), nullable=False),
        sa.Column("transaction_enabled", sa.Boolean(), nullable=False),
        sa.Column("balance_enabled", sa.Boolean(), nullable=False),
        sa.Column("min_balance_delta_usd", sa.Float(), nullable=False),
        sa.Column("min_balance_delta_percent", sa.Float(), nullable=False),
        sa.Column("muted_services", sa.JSON(), nullable=False),
        sa.Column("channels", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_settings_organization_id", "notification_settings", ["organization_id"])
    op.create_index("ix_notification_settings_user_id", "notification_settings", ["user_id"])
    op.create_index(
        "ix_notification_settings_org_user",
        "notification_settings",
        ["organization_id", "user_id"],
        unique=True,
    )

    op.create_table(
        "notification_cursors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("cursor_type", sa.String(length=64), nullable=False),
        sa.Column("source_key", sa.String(length=255), nullable=False),
        sa.Column("cursor_value", sa.String(length=255), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_cursors_organization_id", "notification_cursors", ["organization_id"])
    op.create_index("ix_notification_cursors_user_id", "notification_cursors", ["user_id"])
    op.create_index(
        "ix_notification_cursors_unique",
        "notification_cursors",
        ["organization_id", "user_id", "cursor_type", "source_key"],
        unique=True,
    )

    op.create_table(
        "notification_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("integration_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("send_after_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["integration_id"], ["integrations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_events_organization_id", "notification_events", ["organization_id"])
    op.create_index("ix_notification_events_user_id", "notification_events", ["user_id"])
    op.create_index("ix_notification_events_integration_id", "notification_events", ["integration_id"])
    op.create_index("ix_notification_events_event_type", "notification_events", ["event_type"])
    op.create_index("ix_notification_events_severity", "notification_events", ["severity"])
    op.create_index("ix_notification_events_source", "notification_events", ["source"])
    op.create_index("ix_notification_events_status", "notification_events", ["status"])
    op.create_index("ix_notification_events_created_at", "notification_events", ["created_at"])
    op.create_index(
        "ix_notification_events_unique_dedupe",
        "notification_events",
        ["organization_id", "user_id", "dedupe_key"],
        unique=True,
    )
    op.create_index(
        "ix_notification_events_dispatch",
        "notification_events",
        ["status", "send_after_at", "created_at"],
    )
    op.create_index(
        "ix_notification_events_org_user_created",
        "notification_events",
        ["organization_id", "user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_events_org_user_created", table_name="notification_events")
    op.drop_index("ix_notification_events_dispatch", table_name="notification_events")
    op.drop_index("ix_notification_events_unique_dedupe", table_name="notification_events")
    op.drop_index("ix_notification_events_created_at", table_name="notification_events")
    op.drop_index("ix_notification_events_status", table_name="notification_events")
    op.drop_index("ix_notification_events_source", table_name="notification_events")
    op.drop_index("ix_notification_events_severity", table_name="notification_events")
    op.drop_index("ix_notification_events_event_type", table_name="notification_events")
    op.drop_index("ix_notification_events_integration_id", table_name="notification_events")
    op.drop_index("ix_notification_events_user_id", table_name="notification_events")
    op.drop_index("ix_notification_events_organization_id", table_name="notification_events")
    op.drop_table("notification_events")

    op.drop_index("ix_notification_cursors_unique", table_name="notification_cursors")
    op.drop_index("ix_notification_cursors_user_id", table_name="notification_cursors")
    op.drop_index("ix_notification_cursors_organization_id", table_name="notification_cursors")
    op.drop_table("notification_cursors")

    op.drop_index("ix_notification_settings_org_user", table_name="notification_settings")
    op.drop_index("ix_notification_settings_user_id", table_name="notification_settings")
    op.drop_index("ix_notification_settings_organization_id", table_name="notification_settings")
    op.drop_table("notification_settings")
