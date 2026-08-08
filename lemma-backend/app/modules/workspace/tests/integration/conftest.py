"""Postgres-backed fixtures for sandbox persistence tests.

These need a real Postgres because the schema uses JSONB and postgres-specific
DDL; SQLite would test a different table than the one that ships.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.workspace.infrastructure.models import (
    SandboxInstanceModel,
    SandboxModel,
)
from app.modules.workspace.infrastructure.sandbox_repository import SandboxRepository


def _database_url() -> str:
    return os.getenv(
        "TEST_DATABASE_URL",
        os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/lemma",
        ),
    )


@pytest_asyncio.fixture
async def sandbox_uow_factory() -> AsyncIterator[object]:
    """A unit-of-work factory over real, isolated sandbox tables.

    The service opens several short units of work per operation, so unlike the
    repository fixture this cannot hold one transaction open. It creates the
    tables, hands out real sessions, and truncates afterwards.
    """
    from contextlib import asynccontextmanager

    engine = create_async_engine(_database_url())
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
async def sandbox_repository() -> AsyncIterator[SandboxRepository]:
    """A repository over a real, isolated set of sandbox tables.

    Only the two tables under test are created, and the transaction is rolled
    back afterwards, so this never touches whatever else lives in the database.
    """

    engine = create_async_engine(_database_url(), poolclass=None)
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
