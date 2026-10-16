"""Dealt player card table model."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.models.base import Base


class PlayerCard(Base):
    __tablename__ = "player_cards"
    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "telegram_user_id"],
            ["game_players.game_id", "game_players.telegram_user_id"],
            name="fk_player_cards_game_player",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "game_id",
            "telegram_user_id",
            "category",
            name="uq_player_cards_game_user_category",
        ),
        CheckConstraint(
            "(revealed_at IS NULL AND revealed_round IS NULL "
            "AND reveal_method IS NULL) OR "
            "(revealed_at IS NOT NULL AND revealed_round IS NOT NULL "
            "AND reveal_method IN ('manual', 'automatic'))",
            name="ck_player_cards_reveal_state",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    revealed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revealed_round: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    reveal_method: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    game = relationship("Game", back_populates="cards")
    player = relationship(
        "GamePlayer",
        primaryjoin=(
            "and_(PlayerCard.game_id == GamePlayer.game_id, "
            "PlayerCard.telegram_user_id == GamePlayer.telegram_user_id)"
        ),
        foreign_keys="[PlayerCard.game_id, PlayerCard.telegram_user_id]",
        viewonly=True,
    )
