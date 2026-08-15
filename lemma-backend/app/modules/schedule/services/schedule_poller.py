"""The loop that fires due schedules, on every replica.

One transaction per tick, and that is the point. It takes the due rows with
``FOR UPDATE SKIP LOCKED``, advances their cursors, and stages the fire events
onto the same unit of work. Committing makes all of it real together.

The obvious-looking alternative -- claim and commit, then publish -- is what
this originally did, and it is wrong in a way that only shows up in production:
between the two commits the cursor has moved but the event does not exist, so
the occurrence is silently gone. Nothing retries it, because the row no longer
looks due. We watched exactly that happen: a stale start-up gate rejected every
dispatch while the claims had already committed, and the fires were lost rather
than delayed.

Staging costs one INSERT, not a Redis round trip -- `EventPublisher` writes to
the transactional outbox, and the outbox dispatcher delivers with the lease,
retry and dead-letter behaviour it already has. So keeping it inside the
transaction does not reintroduce the hold this effort exists to remove; there
was never a fan-out here to hold a connection across.

Cadence is a plain sleep rather than a cron, because cron is minute-granular and
these timers are not. A `WAIT_UNTIL` for ninety seconds should fire at ninety
seconds. The poll interval is the worst-case lateness, so it is seconds.

Every replica runs this. Nothing elects a leader; the claim decides who fires
what, which means the answer to "what happens when a replica dies mid-poll" is
"its uncommitted claims roll back and someone else takes them on the next tick".
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone

from app.core.authorization.scope import uow_scope
from app.core.log.log import get_logger
from app.modules.schedule.services.due_schedule_claimer import (
    DEFAULT_CLAIM_LIMIT,
    backfill_missing_cursors,
    claim_due_schedules,
)
from app.core.domain.timers import ClaimedTimer

logger = get_logger(__name__)

#: How late a timer can be, worst case, when nothing is backed up.
DEFAULT_POLL_INTERVAL_SECONDS = 5.0

#: How long to wait after an unexpected failure. Longer than the poll interval
#: so a database that is down is not hammered by every replica at 5Hz.
ERROR_BACKOFF_SECONDS = 30.0


#: Claims due one-shot timers from one module's tables. Injected rather than
#: imported: workflow and agent own their wait tables and already depend on
#: schedule, so reaching into their models from here would make a cycle.
TimerClaimer = Callable[..., Awaitable[list[ClaimedTimer]]]


async def poll_due_schedules_once(
    uow_factory,
    *,
    timer_claimers: Sequence[TimerClaimer] = (),
    now: datetime | None = None,
    limit: int = DEFAULT_CLAIM_LIMIT,
) -> int:
    """Claim and dispatch one batch. Returns how many fires were emitted."""
    from app.modules.schedule.scheduler.events import get_event_emitter

    moment = now or datetime.now(timezone.utc)
    emitter = get_event_emitter()

    async with uow_scope(uow_factory) as uow:
        # Same transaction: a row backfilled here is claimable on the next tick,
        # and one that is already due is claimed below without waiting for one.
        await backfill_missing_cursors(uow.session, now=moment, limit=limit)
        claimed = await claim_due_schedules(uow.session, now=moment, limit=limit)
        timers: list[ClaimedTimer] = []
        for claim_timers in timer_claimers:
            timers.extend(await claim_timers(uow.session, now=moment, limit=limit))

        # Timers ride the same event as schedules: `_dispatch_wake` branches on
        # the payload keys, so rebuilding the payload the old adapters sent
        # leaves the wake path downstream untouched.
        for timer in timers:
            await emitter.stage_scheduled_job_event(
                uow,
                schedule_id=timer.timer_id,
                user_id=timer.user_id,
                payload=timer.payload,
                scheduled_at=timer.fire_at,
            )
        for fire in claimed:
            await emitter.stage_scheduled_job_event(
                uow,
                schedule_id=fire.schedule_id,
                user_id=fire.user_id,
                payload=fire.payload,
                scheduled_at=fire.fire_at,
            )

    return len(timers) + len(claimed)


async def run_schedule_poller(
    uow_factory,
    *,
    timer_claimers: Sequence[TimerClaimer] = (),
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
            await poll_due_schedules_once(
                uow_factory, timer_claimers=timer_claimers
            )
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
