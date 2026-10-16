"""Romantic pair proposal and relationship model."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.models.base import Base

if TYPE_CHECKING:
    from database.models.para_member import ParaMember


class Para(Base):
    __tablename__ = "paras"
    __table_args__ = (
        CheckConstraint(
            "proposer_id <> invitee_id",
            name="ck_paras_different_users",
        ),
        CheckConstraint(
            "status IN ('pending', 'active', 'rejected', 'cancelled', 'expired')",
            name="ck_paras_status",
        ),
        Index(
            "ix_paras_invitee_status",
            "invitee_id",
            "status",
        ),
        Index(
            "ix_paras_proposer_status",
            "proposer_id",
            "status",
        ),
        Index(
            "uq_paras_pending_pair",
            text("least(proposer_id, invitee_id)"),
            text("greatest(proposer_id, invitee_id)"),
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    proposer_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    invitee_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    proposer_display_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    proposer_username: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    members: Mapped[list["ParaMember"]] = relationship(
        back_populates="para",
        cascade="all, delete-orphan",
    )
