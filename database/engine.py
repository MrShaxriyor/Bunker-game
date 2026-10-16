"""SQLAlchemy async engine and session factory configuration."""

import os

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine() -> AsyncEngine:
    """Create the async engine and session factory without connecting."""
    global _engine, _sessionmaker

    if _engine is not None:
        return _engine

    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL topilmadi. Loyihaning .env fayliga "
            "PostgreSQL ulanish manzilini kiriting."
        )

    try:
        url = make_url(database_url)
    except (ArgumentError, TypeError, ValueError):
        raise RuntimeError(
            "DATABASE_URL noto‘g‘ri formatda. "
            "postgresql+asyncpg://... formatidan foydalaning."
        ) from None

    if url.drivername != "postgresql+asyncpg":
        raise RuntimeError(
            "DATABASE_URL postgresql+asyncpg:// bilan boshlanishi kerak."
        )

    _engine = create_async_engine(url)
    _sessionmaker = async_sessionmaker(
        _engine,
        expire_on_commit=False,
    )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the session factory, initializing it if necessary."""
    if _sessionmaker is None:
        init_engine()

    if _sessionmaker is None:
        raise RuntimeError("Database session factory yaratilmadi.")

    return _sessionmaker


async def check_connection() -> None:
    """Explicitly check database connectivity with a lightweight query."""
    engine = init_engine()
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def close_engine() -> None:
    """Dispose the engine and clear its module-level references."""
    global _engine, _sessionmaker

    if _engine is not None:
        await _engine.dispose()

    _engine = None
    _sessionmaker = None
