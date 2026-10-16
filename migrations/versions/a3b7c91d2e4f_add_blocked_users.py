"""add blocked users

Revision ID: a3b7c91d2e4f
Revises: 9faf94e1544c
Create Date: 2026-10-15 00:12:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3b7c91d2e4f"
down_revision: Union[str, Sequence[str], None] = "9faf94e1544c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "blocked_users",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("blocked_by_admin_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "blocked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("telegram_user_id"),
    )


def downgrade() -> None:
    op.drop_table("blocked_users")
