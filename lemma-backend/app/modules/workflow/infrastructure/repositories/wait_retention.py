"""Retention for finished workflow waits.

Separate from the wait repository so the deletion predicate — the thing that
decides what is scaffolding and what is a record — sits on its own and is
readable in one screen.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.workflow.domain.wait import (
    WorkflowRunWaitStatus,
    WorkflowRunWaitType,
)
from app.modules.workflow.infrastructure.models import WorkflowRunWaitModel

#: Waits the engine creates to track its own progress. A completed one is spent
#: scaffolding. ``HUMAN`` is deliberately absent: those rows say who was asked
#: to approve something and what they answered, and no age makes that expendable.
_MACHINE_WAIT_TYPES = (
    WorkflowRunWaitType.FUNCTION.value,
    WorkflowRunWaitType.AGENT.value,
    WorkflowRunWaitType.TIME.value,
)

_TERMINAL_STATUSES = (
    WorkflowRunWaitStatus.COMPLETED.value,
    WorkflowRunWaitStatus.FAILED.value,
    WorkflowRunWaitStatus.CANCELLED.value,
)


async def prune_terminal_machine_waits(
    session_maker: Callable[[], AsyncSession],
    *,
    retention_days: int,
    batch_size: int,
    budget_seconds: float,
    now: datetime | None = None,
) -> int:
    """Delete finished machine waits older than the window, within a budget.

    Drains in batches until a short one says it is done, the same shape as the
    event-delivery sweep — one fixed batch per run is a ceiling the backlog
    outruns, and then the table grows however short the window is.
    """
    if budget_seconds <= 0:
        return 0
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    started = time.monotonic()
    removed = 0
    while True:
        async with session_maker() as session, session.begin():
            claimed = (
                select(WorkflowRunWaitModel.id)
                .where(
                    WorkflowRunWaitModel.wait_type.in_(_MACHINE_WAIT_TYPES),
                    WorkflowRunWaitModel.status.in_(_TERMINAL_STATUSES),
                    WorkflowRunWaitModel.updated_at < cutoff,
                )
                .order_by(WorkflowRunWaitModel.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
                .cte("workflow_wait_retention_batch")
            )
            result = await session.execute(
                delete(WorkflowRunWaitModel).where(
                    WorkflowRunWaitModel.id.in_(select(claimed.c.id))
                )
            )
            batch = int(getattr(result, "rowcount", 0) or 0)
        removed += batch
        if batch < batch_size or (time.monotonic() - started) >= budget_seconds:
            return removed
