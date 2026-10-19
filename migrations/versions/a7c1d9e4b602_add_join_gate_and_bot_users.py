"""add persistent bot registration and join-request gate settings

Revision ID: a7c1d9e4b602
Revises: f4a8c2d91b60
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7c1d9e4b602"
down_revision: Union[str, Sequence[str], None] = "f4a8c2d91b60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bot_users",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("join_request_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("join_request_chat_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("telegram_user_id"),
    )
    op.create_index("ix_bot_users_registered_at", "bot_users", ["registered_at"])
    op.execute(sa.text(
        "INSERT INTO bot_users (telegram_user_id, registered_at) "
        "SELECT telegram_user_id, MIN(first_seen) FROM ("
        "SELECT telegram_user_id, joined_at AS first_seen FROM game_players UNION ALL "
        "SELECT telegram_user_id, created_at AS first_seen FROM para_members UNION ALL "
        "SELECT telegram_user_id, created_at AS first_seen FROM player_wallets UNION ALL "
        "SELECT telegram_user_id, created_at AS first_seen FROM payment_orders"
        ") AS known_users GROUP BY telegram_user_id "
        "ON CONFLICT (telegram_user_id) DO NOTHING"
    ))
    op.create_table(
        "bot_settings",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("mandatory_join_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("join_request_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("join_request_link", sa.String(length=512), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_bot_settings_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(sa.text("INSERT INTO bot_settings (id, mandatory_join_enabled) VALUES (1, false)"))


def downgrade() -> None:
    op.drop_table("bot_settings")
    op.drop_index("ix_bot_users_registered_at", table_name="bot_users")
    op.drop_table("bot_users")
