"""Telegram Stars payment order for a coin package."""

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


class PaymentOrder(Base):
    __tablename__ = "payment_orders"
    __table_args__ = (
        UniqueConstraint(
            "invoice_payload",
            name="uq_payment_orders_invoice_payload",
        ),
        UniqueConstraint(
            "telegram_payment_charge_id",
            name="uq_payment_orders_charge_id",
        ),
        CheckConstraint(
            "price_stars >= 0",
            name="ck_payment_orders_price_stars_nonnegative",
        ),
        CheckConstraint(
            "coins >= 0",
            name="ck_payment_orders_coins_nonnegative",
        ),
        CheckConstraint(
            "status IN ('pending', 'paid', 'failed', 'cancelled', 'refunded')",
            name="ck_payment_orders_status",
        ),
        Index(
            "ix_payment_orders_user_created",
            "telegram_user_id",
            "created_at",
        ),
        Index("ix_payment_orders_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "player_wallets.telegram_user_id",
            name="fk_payment_orders_telegram_user_id_player_wallets",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    coin_package_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "coin_packages.id",
            name="fk_payment_orders_coin_package_id_coin_packages",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    invoice_payload: Mapped[str] = mapped_column(String(255), nullable=False)
    price_stars: Mapped[int] = mapped_column(BigInteger, nullable=False)
    coins: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    telegram_payment_charge_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
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
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )