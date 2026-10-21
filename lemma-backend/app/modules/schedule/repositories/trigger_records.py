"""Aggregated skip records and planned batches for triggers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.schedule.infrastructure.models.trigger import TriggerBatch, TriggerSkip


@dataclass(frozen=True, slots=True)
class SkipRecord:
    id: UUID
    reason: str
    first: str
    last: str
    count: int
    at: datetime


@dataclass(frozen=True, slots=True)
class PlannedBatch:
    first: str
    last: str
    count: int
    created: bool


class TriggerRecords:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_skips(
        self,
        schedule_id: UUID,
        *,
        reason: str,
        first: str,
        last: str,
        count: int,
    ) -> None:
        """One row per (page, reason); a replayed page rewrites its own row."""
        if count <= 0:
            return
        statement = insert(TriggerSkip).values(
            schedule_id=schedule_id,
            reason=reason,
            first=first,
            last=last,
            count=count,
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                constraint="uq_trigger_skips_page",
                set_={
                    "last": statement.excluded.last,
                    "count": statement.excluded.count,
                },
            )
        )

    async def list_skips(
        self,
        schedule_id: UUID,
        *,
        limit: int,
        before: tuple[datetime, UUID] | None = None,
    ) -> list[SkipRecord]:
        """Newest first; ``before`` is the ``(at, id)`` of the last row of the previous page."""
        query = select(TriggerSkip).where(TriggerSkip.schedule_id == schedule_id)
        if before is not None:
            query = query.where(
                tuple_(TriggerSkip.at, TriggerSkip.id) < tuple_(*before)
            )
        rows = await self._session.scalars(
            query.order_by(TriggerSkip.at.desc(), TriggerSkip.id.desc()).limit(limit)
        )
        return [
            SkipRecord(row.id, row.reason, row.first, row.last, row.count, row.at)
            for row in rows.all()
        ]

    async def plan_batch(
        self, schedule_id: UUID, *, first: str, last: str, count: int
    ) -> PlannedBatch:
        """Record the window about to fire, or return the one already recorded.

        The second caller for the same ``first`` -- a replay after a crash --
        gets the original ``last`` back and must fire exactly that window.
        """
        created = await self._session.scalar(
            insert(TriggerBatch)
            .values(schedule_id=schedule_id, first=first, last=last, count=count)
            .on_conflict_do_nothing(constraint="uq_trigger_batches_first")
            .returning(TriggerBatch.id)
        )
        if created is not None:
            return PlannedBatch(first, last, count, True)
        existing = await self._session.scalar(
            select(TriggerBatch).where(
                TriggerBatch.schedule_id == schedule_id, TriggerBatch.first == first
            )
        )
        assert existing is not None
        return PlannedBatch(existing.first, existing.last, existing.count, False)


__all__ = ["PlannedBatch", "SkipRecord", "TriggerRecords"]
