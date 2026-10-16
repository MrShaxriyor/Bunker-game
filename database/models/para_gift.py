"""Historical record of a gift sent between para members."""

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


class ParaGift(Base):
    __tablename__ = "para_gifts"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_para_gifts_idempotency_key",
        ),
        CheckConstraint(
            "sender_id <> recipient_id",
            name="ck_para_gifts_different_users",
        ),
        CheckConstraint(
            "price_coins_snapshot >= 0",
            name="ck_para_gifts_price_coins_snapshot_nonnegative",
        ),
        Index(
            "ix_para_gifts_para_created",
            "para_id",
            "created_at",
        ),
        Index(
            "ix_para_gifts_shop_item_id",
            "shop_item_id",
        ),
        Index(
            "ix_para_gifts_sender_created",
            "sender_id",
            "created_at",
        ),
        Index(
            "ix_para_gifts_recipient_created",
            "recipient_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    para_id: Mapped[int] = mapped_column(
        ForeignKey(
            "paras.id",
            name="fk_para_gifts_para_id_paras",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    sender_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    recipient_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shop_item_id: Mapped[int] = mapped_column(
        ForeignKey(
            "shop_items.id",
            name="fk_para_gifts_shop_item_id_shop_items",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    gift_name_snapshot: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    price_coins_snapshot: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
