"""Persisted Telegram user blocks."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base


class BlockedUser(Base):
    __tablename__ = "blocked_users"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked_by_admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    blocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
