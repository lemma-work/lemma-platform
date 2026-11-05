"""Fixtures for the work runtime's real-Postgres tests (and the shared e2e database)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.infrastructure.work.models import WorkItem, WorkQueue, WorkRateBucket
from app.core.infrastructure.work.registry import WorkRegistry
from app.core.tests.e2e.work_support import WorkDb
from app.modules.test_support.e2e import fixtures as e2e_fixtures

postgres_container = e2e_fixtures.postgres_container
test_database_url = e2e_fixtures.test_database_url
postgres_only_container = e2e_fixtures.postgres_only_container
postgres_only_database_url = e2e_fixtures.postgres_only_database_url


@pytest.fixture
async def work_db(postgres_only_database_url: str) -> AsyncIterator[WorkDb]:
    engine = create_async_engine(
        postgres_only_database_url, pool_size=40, max_overflow=0
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            WorkItem.metadata.create_all,
            tables=[
                WorkItem.__table__,
                WorkQueue.__table__,
                WorkRateBucket.__table__,
            ],
            checkfirst=True,
        )
        await connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS work_e2e_effects ("
                "id bigserial PRIMARY KEY, tag text NOT NULL, item_id uuid)"
            )
        )
        await connection.execute(
            text(
                "TRUNCATE work_items, work_queues, work_rate_buckets, work_e2e_effects"
            )
        )
    yield WorkDb(
        engine, async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    )
    await engine.dispose()


@pytest.fixture
def registry() -> WorkRegistry:
    return WorkRegistry()
