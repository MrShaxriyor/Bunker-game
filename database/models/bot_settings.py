"""Persistent singleton settings managed from the admin panel."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base


class BotSettings(Base):
    __tablename__ = "bot_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_bot_settings_singleton"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    mandatory_join_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    join_request_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    join_request_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
