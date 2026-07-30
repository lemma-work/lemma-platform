from __future__ import annotations

import asyncio
from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from agentbox.persistence.models import Base


config = context.config
if config.config_file_name is not None:
    # Alembic may run in-process in tests and administrative tooling. Do not
    # disable the service's already-configured loggers as a migration side effect.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

database_url = os.getenv("AGENTBOX_DATABASE_URL") or os.getenv(
    "AGENTBOX_STATE_DATABASE_URL"
)
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata
_ROLLOUT_COMPATIBILITY_TABLES = {"processes", "sessions", "python_executions"}


def include_object(object_, name, type_, reflected, compare_to) -> bool:
    """Ignore N-1 compatibility shells during the expand-only release."""

    del compare_to
    if reflected and type_ == "table" and name in _ROLLOUT_COMPATIBILITY_TABLES:
        return False
    table = getattr(object_, "table", None)
    return not (
        reflected
        and table is not None
        and table.name in _ROLLOUT_COMPATIBILITY_TABLES
    )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=include_object,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
