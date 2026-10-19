"""Durable bot registration and verified community access state."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base


class BotUser(Base):
    __tablename__ = "bot_users"
    __table_args__ = (Index("ix_bot_users_registered_at", "registered_at"),)

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    join_request_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    join_request_chat_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
