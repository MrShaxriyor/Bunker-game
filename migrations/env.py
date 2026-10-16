import asyncio
from logging.config import fileConfig
import os
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from alembic.util import CommandError
from dotenv import load_dotenv
from database.models import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def configure_database_url() -> None:
    """Load and validate DATABASE_URL without exposing it in config files."""
    config_path = config.config_file_name
    dotenv_path = (
        Path(config_path).resolve().parent / ".env"
        if config_path is not None
        else Path(".env")
    )
    load_dotenv(dotenv_path=dotenv_path)

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise CommandError(
            "DATABASE_URL topilmadi. Loyihaning .env fayliga "
            "PostgreSQL ulanish manzilini kiriting."
        )

    try:
        url = make_url(database_url)
    except (ArgumentError, TypeError, ValueError):
        raise CommandError(
            "DATABASE_URL noto‘g‘ri formatda. "
            "postgresql+asyncpg://... formatidan foydalaning."
        ) from None

    if url.drivername != "postgresql+asyncpg":
        raise CommandError(
            "DATABASE_URL postgresql+asyncpg:// bilan boshlanishi kerak."
        )

    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


configure_database_url()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
