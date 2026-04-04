"""Baseline for existing schema managed by create_all.

Revision ID: 20260226_0001
Revises:
Create Date: 2026-02-26 00:00:00
"""

from typing import Sequence, Union

from alembic import op

from app.core.database import Base
from app.models import *  # noqa: F401,F403

# revision identifiers, used by Alembic.
revision: str = "20260226_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Keep baseline idempotent for existing DBs and usable for greenfield setups.
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    pass
