"""Game vote table model."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.models.base import Base


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "round_number"],
            ["game_rounds.game_id", "game_rounds.round_number"],
            name="fk_votes_game_round",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["game_id", "voter_id"],
            ["game_players.game_id", "game_players.telegram_user_id"],
            name="fk_votes_voter",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["game_id", "target_id"],
            ["game_players.game_id", "game_players.telegram_user_id"],
            name="fk_votes_target",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "game_id",
            "round_number",
            "stage",
            "voter_id",
            name="uq_votes_one_per_stage",
        ),
        CheckConstraint(
            "stage IN ('elimination', 'confirmation')",
            name="ck_votes_stage",
        ),
        CheckConstraint(
            "(stage = 'elimination' AND choice IS NULL) OR "
            "(stage = 'confirmation' AND choice IN ('yes', 'no'))",
            name="ck_votes_choice_by_stage",
        ),
        CheckConstraint(
            "voter_id <> target_id",
            name="ck_votes_no_self_vote",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(20), nullable=False)
    voter_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    choice: Mapped[str | None] = mapped_column(String(3), nullable=True)

    game = relationship("Game", back_populates="votes")
    round = relationship("GameRound", viewonly=True)
    voter = relationship(
        "GamePlayer",
        primaryjoin=(
            "and_(Vote.game_id == GamePlayer.game_id, "
            "Vote.voter_id == GamePlayer.telegram_user_id)"
        ),
        foreign_keys="[Vote.game_id, Vote.voter_id]",
        viewonly=True,
    )
    target = relationship(
        "GamePlayer",
        primaryjoin=(
            "and_(Vote.game_id == GamePlayer.game_id, "
            "Vote.target_id == GamePlayer.telegram_user_id)"
        ),
        foreign_keys="[Vote.game_id, Vote.target_id]",
        viewonly=True,
    )
