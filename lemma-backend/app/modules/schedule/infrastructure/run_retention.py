"""Bounded retention for the schedule-run ledger.

``schedule_runs`` records every fire of every schedule and nothing ever removed
one, so the table only ever grew. The cost is not the heap it occupies — it is
that every index on the table pays for that growth forever. It is what made the
breaker's failure-streak query read the whole of a schedule's history per run
completion, and it is what the recovery sweep's own index would eventually have
grown into.

Two things keep this safe to run against a live table.

**Only finished runs.** The predicate requires a terminal state *and* a
``completed_at`` past the cutoff. A run still in flight has no ``completed_at``,
so it cannot match however old it is — which matters, because runs sit
legitimately in flight for months at a time, parked on human form waits.

**A window longer than a streak.** ``consecutive_terminal_failures`` counts back
from the newest completed run to decide whether a schedule's breaker should
trip. Deleting rows it would have read could shorten a streak that is still
being counted, so the window has to comfortably exceed the span a live streak
can cover. At ninety days it does: a schedule failing often enough to trip has
already tripped long before its failures age out, and failures from three months
ago should not count toward today's streak in any case.

The drain shape is taken from ``core.infrastructure.events.retention``, including
its lesson — deleting one batch per run lets a table that grows faster than the
batch outrun its own pruner, which is how the outbox once reached several
hundred thousand rows.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.events.retention import delete_batch
from app.modules.schedule.config import schedule_settings
from app.modules.schedule.domain.schedule import ScheduleRunStatus
from app.modules.schedule.infrastructure.models.run import ScheduleRun

# States a run can rest in forever. RECEIVED, PROCESSING and DISPATCHED are
# absent on purpose: a DISPATCHED run whose target has not finished is the
# ledger's normal in-flight state, not an old row.
_TERMINAL_STATUSES = (
    ScheduleRunStatus.COMPLETED.value,
    ScheduleRunStatus.TARGET_FAILED.value,
    ScheduleRunStatus.CANCELLED.value,
    ScheduleRunStatus.FILTERED.value,
    ScheduleRunStatus.DEAD_LETTERED.value,
)


async def prune_schedule_runs(
    session_maker: Callable[[], AsyncSession],
    *,
    now: datetime | None = None,
) -> int:
    """Delete terminal runs past the retention window. Returns rows removed."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=schedule_settings.schedule_run_retention_days)
    batch_size = schedule_settings.schedule_run_retention_batch_size
    budget = schedule_settings.schedule_run_retention_budget_seconds
    started = time.monotonic()

    filters = (
        ScheduleRun.completed_at.is_not(None),
        ScheduleRun.completed_at < cutoff,
        # `target_outcome` is the authoritative ending for a dispatched run and
        # `status` for one that never reached a target; a row needs one of them
        # to count as finished. Checking only `completed_at` would be enough
        # today and would silently stop being enough the moment anything sets a
        # completion time before the outcome is known.
        (
            ScheduleRun.target_outcome.is_not(None)
            | ScheduleRun.status.in_(_TERMINAL_STATUSES)
        ),
    )

    removed = 0
    while True:
        # One batch per transaction: a single transaction across the whole
        # drain would pin a connection and keep every deleted tuple alive for
        # its duration.
        async with session_maker() as session, session.begin():
            batch = await delete_batch(
                session, ScheduleRun, *filters, batch_size=batch_size
            )
        removed += batch
        if batch < batch_size:
            break
        if budget <= 0 or (time.monotonic() - started) >= budget:
            break
    return removed
