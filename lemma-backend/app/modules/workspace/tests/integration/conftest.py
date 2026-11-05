"""Postgres-backed fixtures for sandbox persistence tests.

These need a real Postgres because the schema uses JSONB and postgres-specific
DDL; SQLite would test a different table than the one that ships.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.workspace.infrastructure.models import (
    SandboxInstanceModel,
    SandboxModel,
)
from app.modules.workspace.infrastructure.sandbox_repository import SandboxRepository


#: Every database these fixtures create starts with this, and nothing else is
#: ever opened -- the guard that keeps a ``TRUNCATE ... CASCADE`` away from a
#: developer's own data.
ISOLATED_DATABASE_PREFIX = "lemma_test_workspace_"

_DEFAULT_SERVER_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"


def isolated_database_url(server_url: str, worker: str, pid: int) -> str:
    """A database of this test process's own on the server ``server_url`` names.

    Only the server is taken from the configured URL, never its database: the
    default URL is the dev stack's, and these tests truncate what they create.
    The worker id keeps xdist workers apart, and the pid keeps two concurrent
    runs (two worktrees against one local Postgres) apart.
    """
    name = f"{ISOLATED_DATABASE_PREFIX}{worker}_{pid}".lower()
    return make_url(server_url).set(database=name).render_as_string(hide_password=False)


def _server_url() -> str:
    return (
        os.getenv("TEST_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or (_DEFAULT_SERVER_URL)
    )


def _libpq_url(url: str, *, database: str) -> str:
    return (
        make_url(url)
        .set(drivername="postgresql", database=database)
        .render_as_string(hide_password=False)
    )


@pytest.fixture(scope="session")
def sandbox_database_url(worker_id: str) -> Iterator[str]:
    """Create this worker's database on first use and drop it at session end.

    One database per xdist worker is what makes the truncation in
    ``sandbox_uow_factory`` safe: tests in a worker run one at a time, so the
    only rows a truncate can remove are the ones the previous test left.
    """
    url = isolated_database_url(_server_url(), worker_id, os.getpid())
    name = make_url(url).database
    assert name is not None and name.startswith(ISOLATED_DATABASE_PREFIX)
    maintenance = _libpq_url(url, database="postgres")
    try:
        with psycopg.connect(maintenance, autocommit=True, connect_timeout=5) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            conn.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError as exc:  # pragma: no cover - environment guard
        pytest.skip(f"postgres not reachable for integration tests: {exc}")
    try:
        yield url
    finally:
        with psycopg.connect(maintenance, autocommit=True, connect_timeout=5) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest_asyncio.fixture
async def sandbox_uow_factory(sandbox_database_url: str) -> AsyncIterator[object]:
    """A unit-of-work factory over real, isolated sandbox tables.

    The service opens several short units of work per operation, so unlike the
    repository fixture this cannot hold one transaction open. It creates the
    tables, hands out real sessions, and truncates afterwards -- in this
    worker's own database, so the truncate cannot reach another worker's rows
    or the dev stack's.
    """
    from contextlib import asynccontextmanager

    engine = create_async_engine(sandbox_database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                SandboxModel.metadata.create_all,
                tables=[SandboxModel.__table__, SandboxInstanceModel.__table__],
                checkfirst=True,
            )
    except Exception as exc:  # pragma: no cover - environment guard
        await engine.dispose()
        pytest.skip(f"postgres not reachable for integration tests: {exc}")

    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def factory():
        async with maker() as session:
            uow = SimpleNamespace(
                session=session,
                commit=session.commit,
                rollback=session.rollback,
            )
            try:
                yield uow
            except BaseException:
                await session.rollback()
                raise
            else:
                await session.commit()

    try:
        yield factory
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE sandboxes, sandbox_instances CASCADE")
            )
        await engine.dispose()


@pytest_asyncio.fixture
async def sandbox_repository(
    sandbox_database_url: str,
) -> AsyncIterator[SandboxRepository]:
    """A repository over a real, isolated set of sandbox tables.

    Only the two tables under test are created, and the transaction is rolled
    back afterwards, so this never touches whatever else lives in the database.
    """

    engine = create_async_engine(sandbox_database_url, poolclass=None)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                SandboxModel.metadata.create_all,
                tables=[SandboxModel.__table__, SandboxInstanceModel.__table__],
                checkfirst=True,
            )
    except Exception as exc:  # pragma: no cover - environment guard
        await engine.dispose()
        pytest.skip(f"postgres not reachable for integration tests: {exc}")

    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()
    transaction = await session.begin()
    try:
        yield SandboxRepository(SimpleNamespace(session=session))  # type: ignore[arg-type]
    finally:
        await transaction.rollback()
        await session.close()
        await engine.dispose()
