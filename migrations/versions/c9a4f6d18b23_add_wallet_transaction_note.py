"""add wallet transaction audit note

Revision ID: c9a4f6d18b23
Revises: b7d3e91c4a62
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9a4f6d18b23"
down_revision: Union[str, Sequence[str], None] = "b7d3e91c4a62"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "wallet_transactions",
        sa.Column("note", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wallet_transactions", "note")