"""add coin quantity snapshot to payment orders

Revision ID: b7d3e91c4a62
Revises: e6f2c91a4b70
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7d3e91c4a62"
down_revision: Union[str, Sequence[str], None] = "e6f2c91a4b70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payment_orders",
        sa.Column("coins", sa.BigInteger(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE payment_orders "
            "SET coins = coin_packages.coins "
            "FROM coin_packages "
            "WHERE payment_orders.coin_package_id = coin_packages.id"
        )
    )
    op.alter_column(
        "payment_orders",
        "coins",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_payment_orders_coins_nonnegative",
        "payment_orders",
        "coins >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_payment_orders_coins_nonnegative",
        "payment_orders",
        type_="check",
    )
    op.drop_column("payment_orders", "coins")