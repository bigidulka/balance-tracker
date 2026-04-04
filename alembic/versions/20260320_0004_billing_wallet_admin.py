"""Add payment invoices, ledger, and promo tables.

Revision ID: 20260320_0004
Revises: 20260227_0003
Create Date: 2026-03-20 12:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "20260320_0004"
down_revision: Union[str, None] = "20260227_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("payment_invoices"):
        op.create_table(
            "payment_invoices",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("provider", sa.String(length=50), nullable=False, server_default="cryptobot"),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
            sa.Column("invoice_type", sa.String(length=30), nullable=False, server_default="balance_topup"),
            sa.Column("currency", sa.String(length=10), nullable=False, server_default="USD"),
            sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("asset", sa.String(length=20), nullable=True),
            sa.Column("external_invoice_id", sa.String(length=255), nullable=True),
            sa.Column("external_payload", sa.String(length=255), nullable=True),
            sa.Column("pay_url", sa.Text(), nullable=True),
            sa.Column("bot_invoice_url", sa.Text(), nullable=True),
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("paid_amount", sa.Float(), nullable=True),
            sa.Column("paid_asset", sa.String(length=20), nullable=True),
            sa.Column("paid_usd_amount", sa.Float(), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("provider", "external_invoice_id", name="uq_payment_invoice_provider_external_id"),
        )
        op.create_index("ix_payment_invoices_org_status", "payment_invoices", ["organization_id", "status"], unique=False)
        op.create_index("ix_payment_invoices_external_invoice_id", "payment_invoices", ["external_invoice_id"], unique=False)

    if not _has_table("ledger_entries"):
        op.create_table(
            "ledger_entries",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("entry_type", sa.String(length=20), nullable=False),
            sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("currency", sa.String(length=10), nullable=False, server_default="USD"),
            sa.Column("source_type", sa.String(length=50), nullable=False),
            sa.Column("source_id", sa.String(length=255), nullable=True),
            sa.Column("note", sa.String(length=255), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_ledger_entries_org_created", "ledger_entries", ["organization_id", "created_at"], unique=False)
        op.create_index("ix_ledger_entries_org_source", "ledger_entries", ["organization_id", "source_type", "source_id"], unique=False)

    if not _has_table("promo_codes"):
        op.create_table(
            "promo_codes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("code", sa.String(length=64), nullable=False),
            sa.Column("reward_type", sa.String(length=30), nullable=False),
            sa.Column("reward_value", sa.Float(), nullable=False, server_default="0"),
            sa.Column("reward_currency", sa.String(length=10), nullable=False, server_default="USD"),
            sa.Column("plan_code", sa.String(length=50), nullable=True),
            sa.Column("duration_days", sa.Integer(), nullable=True),
            sa.Column("max_redemptions", sa.Integer(), nullable=True),
            sa.Column("per_org_limit", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("redeemed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_promo_codes_code", "promo_codes", ["code"], unique=True)
        op.create_index("ix_promo_codes_reward_type", "promo_codes", ["reward_type"], unique=False)
        op.create_index("ix_promo_codes_is_active", "promo_codes", ["is_active"], unique=False)

    if not _has_table("promo_redemptions"):
        op.create_table(
            "promo_redemptions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("promo_code_id", sa.Integer(), sa.ForeignKey("promo_codes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="applied"),
            sa.Column("reward_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("promo_code_id", "organization_id", "status", name="uq_promo_redemptions_code_org_status"),
        )
        op.create_index("ix_promo_redemptions_org_created", "promo_redemptions", ["organization_id", "created_at"], unique=False)


def downgrade() -> None:
    if _has_table("promo_redemptions"):
        op.drop_index("ix_promo_redemptions_org_created", table_name="promo_redemptions")
        op.drop_table("promo_redemptions")
    if _has_table("promo_codes"):
        op.drop_index("ix_promo_codes_is_active", table_name="promo_codes")
        op.drop_index("ix_promo_codes_reward_type", table_name="promo_codes")
        op.drop_index("ix_promo_codes_code", table_name="promo_codes")
        op.drop_table("promo_codes")
    if _has_table("ledger_entries"):
        op.drop_index("ix_ledger_entries_org_source", table_name="ledger_entries")
        op.drop_index("ix_ledger_entries_org_created", table_name="ledger_entries")
        op.drop_table("ledger_entries")
    if _has_table("payment_invoices"):
        op.drop_index("ix_payment_invoices_external_invoice_id", table_name="payment_invoices")
        op.drop_index("ix_payment_invoices_org_status", table_name="payment_invoices")
        op.drop_table("payment_invoices")
