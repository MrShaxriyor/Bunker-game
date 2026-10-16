"""A player's owned Survival Shop items."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base


class PlayerInventory(Base):
    __tablename__ = "player_inventory"
    __table_args__ = (
        UniqueConstraint(
            "telegram_user_id",
            "shop_item_id",
            name="uq_player_inventory_user_item",
        ),
        CheckConstraint(
            "quantity > 0",
            name="ck_player_inventory_quantity_positive",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "player_wallets.telegram_user_id",
            name="fk_player_inventory_telegram_user_id_player_wallets",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    shop_item_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "shop_items.id",
            name="fk_player_inventory_shop_item_id_shop_items",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
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