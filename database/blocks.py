"""Shared persistence helpers for blocked Telegram accounts."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import BlockedUser


async def lock_user_for_block_check(
    session: AsyncSession,
    telegram_user_id: int,
) -> None:
    await session.execute(select(func.pg_advisory_xact_lock(telegram_user_id)))


async def is_user_blocked(
    session: AsyncSession,
    telegram_user_id: int,
) -> bool:
    return (
        await session.scalar(
            select(BlockedUser.telegram_user_id).where(
                BlockedUser.telegram_user_id == telegram_user_id
            )
        )
        is not None
    )
