"""Game table model."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    text,
    CheckConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.models.base import Base

if TYPE_CHECKING:
    from database.models.game_player import GamePlayer
    from database.models.game_round import GameRound
    from database.models.player_card import PlayerCard
    from database.models.vote import Vote


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        CheckConstraint(
            "status IN ('lobby', 'active', 'completed', 'cancelled')",
            name="ck_games_status",
        ),
        CheckConstraint(
            "current_round > 0",
            name="ck_games_current_round_positive",
        ),
        CheckConstraint(
            "version > 0",
            name="ck_games_version_positive",
        ),
        Index(
            "uq_games_one_open_game_per_chat",
            "chat_id",
            unique=True,
            postgresql_where=text("status IN ('lobby', 'active')"),
        ),
        Index("ix_games_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    host_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="lobby",
        server_default="lobby",
    )
    phase: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="lobby",
        server_default="lobby",
    )
    current_round: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    lobby_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    confirmation_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    first_winner_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    second_winner_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    phase_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    phase_deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )

    players: Mapped[list["GamePlayer"]] = relationship(
        back_populates="game",
        cascade="all, delete-orphan",
    )
    cards: Mapped[list["PlayerCard"]] = relationship(
        back_populates="game",
        cascade="all, delete-orphan",
    )
    rounds: Mapped[list["GameRound"]] = relationship(
        back_populates="game",
        cascade="all, delete-orphan",
    )
    votes: Mapped[list["Vote"]] = relationship(
        back_populates="game",
        cascade="all, delete-orphan",
    )
