"""add para tables

Revision ID: c4e8a6b19d32
Revises: a3b7c91d2e4f
Create Date: 2026-10-15 01:17:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4e8a6b19d32"
down_revision: Union[str, Sequence[str], None] = "a3b7c91d2e4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paras",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("proposer_id", sa.BigInteger(), nullable=False),
        sa.Column("invitee_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "proposer_id <> invitee_id",
            name="ck_paras_different_users",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'rejected', 'cancelled', 'expired')",
            name="ck_paras_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_paras_invitee_status",
        "paras",
        ["invitee_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_paras_proposer_status",
        "paras",
        ["proposer_id", "status"],
        unique=False,
    )
    op.create_table(
        "para_members",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("para_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("partner_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "telegram_user_id <> partner_id",
            name="ck_para_members_different_users",
        ),
        sa.ForeignKeyConstraint(
            ["para_id"],
            ["paras.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "para_id",
            "telegram_user_id",
            name="uq_para_members_para_user",
        ),
    )
    op.create_index(
        "uq_para_members_one_active_para_per_user",
        "para_members",
        ["telegram_user_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "ix_para_members_active_partner",
        "para_members",
        ["partner_id"],
        unique=False,
        postgresql_where=sa.text("ended_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_para_members_active_partner",
        table_name="para_members",
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.drop_index(
        "uq_para_members_one_active_para_per_user",
        table_name="para_members",
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.drop_table("para_members")
    op.drop_index("ix_paras_proposer_status", table_name="paras")
    op.drop_index("ix_paras_invitee_status", table_name="paras")
    op.drop_table("paras")
