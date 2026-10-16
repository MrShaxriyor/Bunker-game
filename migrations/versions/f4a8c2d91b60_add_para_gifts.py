"""add historical para gifts

Revision ID: f4a8c2d91b60
Revises: c9a4f6d18b23
Create Date: 2026-10-15 23:51:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4a8c2d91b60"
down_revision: Union[str, Sequence[str], None] = "c9a4f6d18b23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "para_gifts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("para_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_id", sa.BigInteger(), nullable=False),
        sa.Column("recipient_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_item_id", sa.BigInteger(), nullable=False),
        sa.Column("gift_name_snapshot", sa.String(length=255), nullable=False),
        sa.Column("price_coins_snapshot", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sender_id <> recipient_id",
            name="ck_para_gifts_different_users",
        ),
        sa.CheckConstraint(
            "price_coins_snapshot >= 0",
            name="ck_para_gifts_price_coins_snapshot_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["para_id"],
            ["paras.id"],
            name="fk_para_gifts_para_id_paras",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shop_item_id"],
            ["shop_items.id"],
            name="fk_para_gifts_shop_item_id_shop_items",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_para_gifts_idempotency_key",
        ),
    )
    op.create_index(
        "ix_para_gifts_para_created",
        "para_gifts",
        ["para_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_para_gifts_shop_item_id",
        "para_gifts",
        ["shop_item_id"],
        unique=False,
    )
    op.create_index(
        "ix_para_gifts_sender_created",
        "para_gifts",
        ["sender_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_para_gifts_recipient_created",
        "para_gifts",
        ["recipient_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_para_gifts_recipient_created", table_name="para_gifts")
    op.drop_index("ix_para_gifts_sender_created", table_name="para_gifts")
    op.drop_index("ix_para_gifts_shop_item_id", table_name="para_gifts")
    op.drop_index("ix_para_gifts_para_created", table_name="para_gifts")
    op.drop_table("para_gifts")
