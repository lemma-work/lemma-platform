"""Driving ``file.process`` items in a test, against the real tables.

Uploads go through the API, which writes the work item in the upload's
transaction; this runs the runtime's own claim/execute/reap code on them with
budgets a test can exhaust in seconds, and reads back what the rows and items
say. The extractor is the fake HTTP server at the Kreuzberg boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.infrastructure.work import (
    Exponential,
    RetryPolicy,
    WorkClass,
    WorkRegistry,
)
from app.core.infrastructure.work.testing import make_due, run_until_idle
from app.modules.datastore.events.file_work import FILE_PROCESS, register_file_work
from app.modules.datastore.tests.e2e.fake_document_processors import (
    FakeDocumentProcessorServer,
)

#: Short enough that a test exhausts every budget in seconds.
TEST_RETRY = RetryPolicy(
    max_attempts=3,
    max_lease_losses=3,
    unavailable_budget=timedelta(seconds=30),
    backoff=Exponential(base=timedelta(seconds=1), cap=timedelta(seconds=1), jitter=0),
)


def file_work_registry(
    *, retry: RetryPolicy = TEST_RETRY, lease: timedelta | None = None
) -> WorkRegistry:
    registry = WorkRegistry()
    register_file_work(registry, retry=retry, lease=lease)
    return registry


@dataclass
class FileWork:
    session_maker: async_sessionmaker[AsyncSession]
    registry: WorkRegistry
    server: FakeDocumentProcessorServer

    async def run_once(self) -> int:
        """Run what is due now, without pulling backoffs forward."""
        return await run_until_idle(
            self.session_maker, registry=self.registry, classes=[WorkClass.INGESTION]
        )

    async def drain(self, *, rounds: int = 20) -> int:
        """Run until no ingestion item is queued: every backoff pulled to now."""
        executed = 0
        for _ in range(rounds):
            executed += await self.run_once()
            if not await self.queued():
                return executed
            await make_due(self.session_maker)
        raise AssertionError(f"ingestion work still queued after {rounds} rounds")

    async def queued(self) -> int:
        return int(
            await self.scalar(
                "SELECT count(*) FROM work_items WHERE class = 'ingestion' "
                "AND state = 'queued'"
            )
        )

    async def items(self, file_id: UUID | str) -> list[dict[str, Any]]:
        async with self.session_maker() as session:
            rows = await session.execute(
                text(
                    "SELECT state, attempts, lease_losses, error_class, last_error, "
                    "first_unavailable_at, dedup_key FROM work_items "
                    "WHERE kind = :kind AND payload ->> 'file_id' = :file_id "
                    "ORDER BY created_at"
                ),
                {"kind": FILE_PROCESS, "file_id": str(file_id)},
            )
            return [dict(row._mapping) for row in rows]

    async def row(self, file_id: UUID | str) -> dict[str, Any]:
        async with self.session_maker() as session:
            result = await session.execute(
                text(
                    "SELECT status, last_processing_error, processing_attempts, "
                    "content_sha256, indexed_sha256, storage_layout, parent_id, path "
                    "FROM datastore_files WHERE id = :id"
                ),
                {"id": str(file_id)},
            )
            return dict(result.one()._mapping)

    async def scalar(self, sql: str, **params: object) -> object:
        async with self.session_maker() as session:
            return await session.scalar(text(sql), params)

    async def execute(self, sql: str, **params: object) -> None:
        async with self.session_maker() as session:
            await session.execute(text(sql), params)
            await session.commit()

    def extractions(self, filename: str) -> int:
        return self.server.requests[f"kreuzberg:{filename}"]
