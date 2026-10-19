"""One owned inventory copy for each received Para Gift."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base


class ParaGiftInventory(Base):
    __tablename__ = "para_gift_inventory"
    __table_args__ = (
        UniqueConstraint("para_gift_id", name="uq_para_gift_inventory_gift"),
        CheckConstraint(
            "original_price_coins >= 0",
            name="ck_para_gift_inventory_original_price_nonnegative",
        ),
        CheckConstraint(
            "(sold_at IS NULL AND resale_price_coins IS NULL) OR "
            "(sold_at IS NOT NULL AND resale_price_coins IS NOT NULL)",
            name="ck_para_gift_inventory_sale_state",
        ),
        CheckConstraint(
            "resale_price_coins IS NULL OR resale_price_coins >= 0",
            name="ck_para_gift_inventory_resale_price_nonnegative",
        ),
        Index("ix_para_gift_inventory_owner_received", "owner_id", "received_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    para_gift_id: Mapped[int] = mapped_column(
        ForeignKey(
            "para_gifts.id",
            name="fk_para_gift_inventory_gift_para_gifts",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sender_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    recipient_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sender_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    shop_item_id: Mapped[int] = mapped_column(
        ForeignKey(
            "shop_items.id",
            name="fk_para_gift_inventory_shop_item_shop_items",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    gift_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    image_emoji_snapshot: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="🎁"
    )
    original_price_coins: Mapped[int] = mapped_column(BigInteger, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resale_price_coins: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
