"""Bounded batch deletes for retention sweeps."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession


async def delete_batch(
    session: AsyncSession,
    model,
    *filters,
    batch_size: int,
) -> int:
    """Delete one bounded batch, skipping rows another sweep holds.

    Shared with the schedule-run pruner rather than reimplemented there: the
    ``SKIP LOCKED`` claim and the id-ordered CTE are the parts that make a
    pruner safe to run concurrently with the writers, and are worth having in
    one place.
    """
    claimed = (
        select(model.id)
        .where(*filters)
        .order_by(model.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
        .cte("retention_batch")
    )
    result = await session.execute(
        delete(model).where(model.id.in_(select(claimed.c.id)))
    )
    return int(getattr(result, "rowcount", 0) or 0)
