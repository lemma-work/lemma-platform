"""The loop that fires due schedules, on every replica.

Two phases, and the split is the point:

1. **Claim** — one short transaction that takes the due rows with
   ``FOR UPDATE SKIP LOCKED`` and advances their cursors. Ends immediately.
2. **Dispatch** — emit a fire event per claim, holding no pooled connection.

Doing it the other way round is the bug this whole effort exists to remove: a
fan-out of Redis publishes with a connection checked out and the database asked
nothing. Here the connection is back in the pool before the first event is sent.

Cadence is a plain sleep rather than a cron, because cron is minute-granular and
these timers are not. A `WAIT_UNTIL` for ninety seconds should fire at ninety
seconds. The poll interval is the worst-case lateness, so it is seconds.

Every replica runs this. Nothing elects a leader; the claim decides who fires
what, which means the answer to "what happens when a replica dies mid-poll" is
"its uncommitted claims roll back and someone else takes them on the next tick".
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.core.authorization.scope import uow_scope
from app.core.log.log import get_logger
from app.modules.schedule.services.due_schedule_claimer import (
    DEFAULT_CLAIM_LIMIT,
    backfill_missing_cursors,
    claim_due_schedules,
)
from app.modules.schedule.services.due_timer_claimer import (
    claim_due_snooze_waits,
    claim_due_workflow_waits,
)

logger = get_logger(__name__)

#: How late a timer can be, worst case, when nothing is backed up.
DEFAULT_POLL_INTERVAL_SECONDS = 5.0

#: How long to wait after an unexpected failure. Longer than the poll interval
#: so a database that is down is not hammered by every replica at 5Hz.
ERROR_BACKOFF_SECONDS = 30.0


async def poll_due_schedules_once(
    uow_factory,
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_CLAIM_LIMIT,
) -> int:
    """Claim and dispatch one batch. Returns how many fires were emitted."""
    from app.modules.schedule.scheduler.events import get_event_emitter

    moment = now or datetime.now(timezone.utc)

    async with uow_scope(uow_factory) as uow:
        # Same transaction: a row backfilled here is claimable on the next tick,
        # and one that is already due is claimed below without waiting for one.
        await backfill_missing_cursors(uow.session, now=moment, limit=limit)
        claimed = await claim_due_schedules(uow.session, now=moment, limit=limit)
        timers = [
            *await claim_due_workflow_waits(uow.session, now=moment, limit=limit),
            *await claim_due_snooze_waits(uow.session, now=moment, limit=limit),
        ]

    if not claimed and not timers:
        return 0

    emitter = get_event_emitter()
    dispatched = 0
    for timer in timers:
        try:
            # Timers ride the same event as schedules: `_dispatch_wake` branches
            # on the payload keys, so rebuilding the payload the old adapters
            # sent means the wake path downstream is untouched.
            await emitter.emit_scheduled_job_event(
                schedule_id=timer.timer_id,
                user_id=timer.user_id,
                payload=timer.payload,
                scheduled_at=timer.fire_at,
            )
            dispatched += 1
        except Exception:
            # Unlike a schedule, this one IS retried: the lease expires and
            # another replica picks it up, because a timer that never fires is
            # a workflow stuck forever rather than one missed occurrence.
            logger.warning(
                "schedule.poller.timer_dispatch_failed.degraded",
                timer_id=str(timer.timer_id),
                exc_info=True,
            )
    for fire in claimed:
        try:
            await emitter.emit_scheduled_job_event(
                schedule_id=fire.schedule_id,
                user_id=fire.user_id,
                payload=fire.payload,
                scheduled_at=fire.fire_at,
            )
            dispatched += 1
        except Exception:
            # The claim is already committed, so this occurrence will not be
            # retried by the poller. That is deliberate: the alternative is
            # holding the claim open across the dispatch, which reintroduces
            # the hold and risks firing twice on a retry. Recovery belongs to
            # the schedule-run recovery sweep, which reads the ledger.
            logger.warning(
                "schedule.poller.dispatch_failed.degraded",
                schedule_id=str(fire.schedule_id),
                exc_info=True,
            )
    return dispatched


async def run_schedule_poller(
    uow_factory,
    *,
    interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    service_name: str = "lemma-worker",
) -> None:
    """Poll forever. Cancelled by the caller at shutdown."""
    logger.info(
        "schedule.poller.started",
        service=service_name,
        interval_ms=round(interval_seconds * 1000),
    )
    while True:
        try:
            await poll_due_schedules_once(uow_factory)
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("schedule.poller.stopped", service=service_name)
            raise
        except Exception:
            # One bad tick must not end the loop -- a poller that dies on a
            # transient database error stops every schedule in the fleet, and
            # nothing restarts it until the process does.
            logger.warning("schedule.poller.tick_failed.degraded", exc_info=True)
            await asyncio.sleep(ERROR_BACKOFF_SECONDS)
