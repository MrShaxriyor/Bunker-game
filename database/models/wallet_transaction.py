"""Append-only coin movement record for a player wallet."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_wallet_transactions_idempotency_key",
        ),
        CheckConstraint(
            "direction IN ('credit', 'debit')",
            name="ck_wallet_transactions_direction",
        ),
        CheckConstraint(
            "amount_coins > 0",
            name="ck_wallet_transactions_amount_positive",
        ),
        Index(
            "ix_wallet_transactions_user_created",
            "telegram_user_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "player_wallets.telegram_user_id",
            name=(
                "fk_wallet_transactions_telegram_user_id_player_wallets"
            ),
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    amount_coins: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=func.now(),
    )