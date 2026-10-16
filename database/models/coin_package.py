"""Survival Shop coin packages purchasable with Telegram Stars."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base


class CoinPackage(Base):
    __tablename__ = "coin_packages"
    __table_args__ = (
        UniqueConstraint("code", name="uq_coin_packages_code"),
        CheckConstraint(
            "coins >= 0",
            name="ck_coin_packages_coins_nonnegative",
        ),
        CheckConstraint(
            "price_stars >= 0",
            name="ck_coin_packages_price_stars_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    coins: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_stars: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
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