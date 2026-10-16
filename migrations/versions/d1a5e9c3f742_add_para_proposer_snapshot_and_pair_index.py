"""add para proposer snapshot and pending pair index

Revision ID: d1a5e9c3f742
Revises: c4e8a6b19d32
Create Date: 2026-10-15 01:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1a5e9c3f742"
down_revision: Union[str, Sequence[str], None] = "c4e8a6b19d32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "paras",
        sa.Column("proposer_display_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "paras",
        sa.Column("proposer_username", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "uq_paras_pending_pair",
        "paras",
        [
            sa.text("least(proposer_id, invitee_id)"),
            sa.text("greatest(proposer_id, invitee_id)"),
        ],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_paras_pending_pair",
        table_name="paras",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_column("paras", "proposer_username")
    op.drop_column("paras", "proposer_display_name")
