"""add Survival Shop tables

Revision ID: e6f2c91a4b70
Revises: d1a5e9c3f742
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e6f2c91a4b70"
down_revision: Union[str, Sequence[str], None] = "d1a5e9c3f742"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shop_items",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("item_type", sa.String(length=32), nullable=False),
        sa.Column("price_coins", sa.BigInteger(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "price_coins >= 0",
            name="ck_shop_items_price_coins_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_shop_items_code"),
    )
    op.create_table(
        "player_wallets",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "balance_coins",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "balance_coins >= 0",
            name="ck_player_wallets_balance_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "telegram_user_id",
            name="uq_player_wallets_telegram_user_id",
        ),
    )
    op.create_table(
        "coin_packages",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("coins", sa.BigInteger(), nullable=False),
        sa.Column("price_stars", sa.BigInteger(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "coins >= 0",
            name="ck_coin_packages_coins_nonnegative",
        ),
        sa.CheckConstraint(
            "price_stars >= 0",
            name="ck_coin_packages_price_stars_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_coin_packages_code"),
    )
    op.create_table(
        "wallet_transactions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("amount_coins", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "direction IN ('credit', 'debit')",
            name="ck_wallet_transactions_direction",
        ),
        sa.CheckConstraint(
            "amount_coins > 0",
            name="ck_wallet_transactions_amount_positive",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["player_wallets.telegram_user_id"],
            name="fk_wallet_transactions_telegram_user_id_player_wallets",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_wallet_transactions_idempotency_key",
        ),
    )
    op.create_index(
        "ix_wallet_transactions_user_created",
        "wallet_transactions",
        ["telegram_user_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "player_inventory",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_item_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name="ck_player_inventory_quantity_positive",
        ),
        sa.ForeignKeyConstraint(
            ["shop_item_id"],
            ["shop_items.id"],
            name="fk_player_inventory_shop_item_id_shop_items",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["player_wallets.telegram_user_id"],
            name="fk_player_inventory_telegram_user_id_player_wallets",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "telegram_user_id",
            "shop_item_id",
            name="uq_player_inventory_user_item",
        ),
    )
    op.create_index(
        "ix_player_inventory_shop_item_id",
        "player_inventory",
        ["shop_item_id"],
        unique=False,
    )
    op.create_table(
        "payment_orders",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("coin_package_id", sa.BigInteger(), nullable=False),
        sa.Column("invoice_payload", sa.String(length=255), nullable=False),
        sa.Column("price_stars", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "telegram_payment_charge_id",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "price_stars >= 0",
            name="ck_payment_orders_price_stars_nonnegative",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'paid', 'failed', 'cancelled', 'refunded')",
            name="ck_payment_orders_status",
        ),
        sa.ForeignKeyConstraint(
            ["coin_package_id"],
            ["coin_packages.id"],
            name="fk_payment_orders_coin_package_id_coin_packages",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["player_wallets.telegram_user_id"],
            name="fk_payment_orders_telegram_user_id_player_wallets",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "invoice_payload",
            name="uq_payment_orders_invoice_payload",
        ),
        sa.UniqueConstraint(
            "telegram_payment_charge_id",
            name="uq_payment_orders_charge_id",
        ),
    )
    op.create_index(
        "ix_payment_orders_user_created",
        "payment_orders",
        ["telegram_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_payment_orders_status",
        "payment_orders",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_payment_orders_status", table_name="payment_orders")
    op.drop_index("ix_payment_orders_user_created", table_name="payment_orders")
    op.drop_table("payment_orders")
    op.drop_index("ix_player_inventory_shop_item_id", table_name="player_inventory")
    op.drop_table("player_inventory")
    op.drop_index(
        "ix_wallet_transactions_user_created",
        table_name="wallet_transactions",
    )
    op.drop_table("wallet_transactions")
    op.drop_table("coin_packages")
    op.drop_table("player_wallets")
    op.drop_table("shop_items")