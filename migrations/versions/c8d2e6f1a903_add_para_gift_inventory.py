"""add per-copy Para Gifts inventory and resale state

Revision ID: c8d2e6f1a903
Revises: a7c1d9e4b602
Create Date: 2026-10-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8d2e6f1a903"
down_revision: Union[str, Sequence[str], None] = "a7c1d9e4b602"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "para_gift_inventory",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("para_gift_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_id", sa.BigInteger(), nullable=False),
        sa.Column("recipient_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_name_snapshot", sa.String(length=255), nullable=False),
        sa.Column("recipient_name_snapshot", sa.String(length=255), nullable=False),
        sa.Column("shop_item_id", sa.BigInteger(), nullable=False),
        sa.Column("gift_name_snapshot", sa.String(length=255), nullable=False),
        sa.Column(
            "image_emoji_snapshot",
            sa.String(length=32),
            server_default="🎁",
            nullable=False,
        ),
        sa.Column("original_price_coins", sa.BigInteger(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sold_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resale_price_coins", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "original_price_coins >= 0",
            name="ck_para_gift_inventory_original_price_nonnegative",
        ),
        sa.CheckConstraint(
            "(sold_at IS NULL AND resale_price_coins IS NULL) OR "
            "(sold_at IS NOT NULL AND resale_price_coins IS NOT NULL)",
            name="ck_para_gift_inventory_sale_state",
        ),
        sa.CheckConstraint(
            "resale_price_coins IS NULL OR resale_price_coins >= 0",
            name="ck_para_gift_inventory_resale_price_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["para_gift_id"],
            ["para_gifts.id"],
            name="fk_para_gift_inventory_gift_para_gifts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shop_item_id"],
            ["shop_items.id"],
            name="fk_para_gift_inventory_shop_item_shop_items",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "para_gift_id",
            name="uq_para_gift_inventory_gift",
        ),
    )
    op.create_index(
        "ix_para_gift_inventory_owner_received",
        "para_gift_inventory",
        ["owner_id", "received_at"],
        unique=False,
    )

    # Existing gift history represents gifts already received. Preserve each
    # copy as an unsold inventory row instead of dropping or rewriting history.
    op.execute(
        sa.text(
            """INSERT INTO para_gift_inventory (
                para_gift_id, owner_id, sender_id, recipient_id,
                sender_name_snapshot, recipient_name_snapshot, shop_item_id,
                gift_name_snapshot, image_emoji_snapshot,
                original_price_coins, received_at
            )
            SELECT g.id, g.recipient_id, g.sender_id, g.recipient_id,
                   COALESCE((SELECT pm.display_name FROM para_members pm
                       WHERE pm.para_id = g.para_id AND pm.telegram_user_id = g.sender_id), g.sender_id::text),
                   COALESCE((SELECT pm.display_name FROM para_members pm
                       WHERE pm.para_id = g.para_id AND pm.telegram_user_id = g.recipient_id), g.recipient_id::text),
                   g.shop_item_id, g.gift_name_snapshot,
                   CASE WHEN position(' ' in g.gift_name_snapshot) > 0
                       THEN split_part(g.gift_name_snapshot, ' ', 1) ELSE '🎁' END,
                   g.price_coins_snapshot, g.created_at
            FROM para_gifts g"""
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "Para Gifts inventory is irreversible because it contains ownership "
        "and resale history; preserve it before any manual rollback."
    )
