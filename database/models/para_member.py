"""Snapshot of a user's membership in a para."""

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.models.base import Base


class ParaMember(Base):
    __tablename__ = "para_members"
    __table_args__ = (
        UniqueConstraint(
            "para_id",
            "telegram_user_id",
            name="uq_para_members_para_user",
        ),
        CheckConstraint(
            "telegram_user_id <> partner_id",
            name="ck_para_members_different_users",
        ),
        Index(
            "uq_para_members_one_active_para_per_user",
            "telegram_user_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        Index(
            "ix_para_members_active_partner",
            "partner_id",
            postgresql_where=text("ended_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    para_id: Mapped[int] = mapped_column(
        ForeignKey("paras.id", ondelete="CASCADE"),
        nullable=False,
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    partner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    para = relationship("Para", back_populates="members")
