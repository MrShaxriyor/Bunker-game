"""Persistent registration and mandatory join-request gate operations."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from database.engine import get_sessionmaker
from database.models import BotSettings, BotUser

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JoinSettings:
    enabled: bool = False
    chat_id: int | None = None
    invite_link: str | None = None


async def register_user(telegram_user_id: int) -> None:
    async with get_sessionmaker()() as session:
        async with session.begin():
            await session.execute(
                pg_insert(BotUser)
                .values(telegram_user_id=telegram_user_id)
                .on_conflict_do_nothing(index_elements=[BotUser.telegram_user_id])
            )


async def get_join_settings() -> JoinSettings:
    async with get_sessionmaker()() as session:
        row = await session.get(BotSettings, 1)
        if row is None:
            return JoinSettings()
        return JoinSettings(
            enabled=row.mandatory_join_enabled,
            chat_id=row.join_request_chat_id,
            invite_link=row.join_request_link,
        )


async def get_user_access(telegram_user_id: int) -> tuple[JoinSettings, bool]:
    async with get_sessionmaker()() as session:
        settings = await session.get(BotSettings, 1)
        if settings is None:
            return JoinSettings(), False
        required = JoinSettings(
            settings.mandatory_join_enabled,
            settings.join_request_chat_id,
            settings.join_request_link,
        )
        user = await session.get(BotUser, telegram_user_id)
        verified = bool(
            user
            and user.join_request_chat_id == required.chat_id
            and user.join_request_verified_at is not None
        )
        return required, verified


async def record_chat_membership(telegram_user_id: int, chat_id: int) -> bool:
    """Persist membership only after Telegram reports an accepted join."""
    async with get_sessionmaker()() as session:
        async with session.begin():
            settings = await session.scalar(
                select(BotSettings).where(BotSettings.id == 1).with_for_update()
            )
            if (
                settings is None
                or not settings.mandatory_join_enabled
                or settings.join_request_chat_id != chat_id
            ):
                return False
            await session.execute(
                pg_insert(BotUser)
                .values(
                    telegram_user_id=telegram_user_id,
                    join_request_verified_at=datetime.now(timezone.utc),
                    join_request_chat_id=chat_id,
                )
                .on_conflict_do_update(
                    index_elements=[BotUser.telegram_user_id],
                    set_={
                        "join_request_verified_at": datetime.now(timezone.utc),
                        "join_request_chat_id": chat_id,
                    },
                )
            )
            return True


async def update_join_settings(
    *,
    admin_id: int,
    enabled: bool | None = None,
    chat_id: int | None = None,
    invite_link: str | None = None,
) -> JoinSettings:
    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                row = await session.scalar(
                    select(BotSettings).where(BotSettings.id == 1).with_for_update()
                )
                if row is None:
                    row = BotSettings(id=1, mandatory_join_enabled=False)
                    session.add(row)
                if enabled is not None:
                    row.mandatory_join_enabled = enabled
                if chat_id is not None:
                    row.join_request_chat_id = chat_id
                if invite_link is not None:
                    row.join_request_link = invite_link
                row.updated_by = admin_id
                row.updated_at = datetime.now(timezone.utc)
                await session.flush()
                return JoinSettings(
                    row.mandatory_join_enabled,
                    row.join_request_chat_id,
                    row.join_request_link,
                )
    except SQLAlchemyError as error:
        logger.error("Join settings update failed: error_type=%s", type(error).__name__)
        raise
