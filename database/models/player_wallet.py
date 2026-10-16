"""A player's Survival Shop coin wallet."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base


class PlayerWallet(Base):
    __tablename__ = "player_wallets"
    __table_args__ = (
        UniqueConstraint(
            "telegram_user_id",
            name="uq_player_wallets_telegram_user_id",
        ),
        CheckConstraint(
            "balance_coins >= 0",
            name="ck_player_wallets_balance_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_coins: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
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