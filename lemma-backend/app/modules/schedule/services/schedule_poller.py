"""The loop that fires due wait timers, on every replica.

TIME schedules and workflow ``WAIT_UNTIL`` timers no longer pass through here:
each is a work item due at its moment. What is left is agent ``wait_for`` TIME
waits, kept in the agent module's own table -- until PR 3 moves them to
``agent.wait.fire`` items and this loop is deleted. One transaction
per tick claims them with ``FOR UPDATE SKIP LOCKED`` and stages each fire onto
the same unit of work, so a claim and its fire commit together.

Cadence is a plain sleep rather than a cron, because cron is minute-granular
and these timers are not. Every replica runs this; the claim decides who fires
what.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone

from app.core.authorization.scope import uow_scope
from app.core.log.log import get_logger
from app.core.origin import Origin, OriginKind, origin_scope
from app.core.domain.timers import ClaimedTimer

logger = get_logger(__name__)

#: How late a timer can be, worst case, when nothing is backed up.
DEFAULT_POLL_INTERVAL_SECONDS = 5.0

DEFAULT_CLAIM_LIMIT = 100

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
    """Claim and fire one batch of due timers. Returns how many fired."""
    from app.modules.schedule.scheduler.events import get_event_emitter

    moment = now or datetime.now(timezone.utc)
    emitter = get_event_emitter()

    # Where SCHEDULE origin is born. Nothing upstream can supply it: this runs on
    # a timer in a background loop with no caller, and `create_background_task`
    # gives that loop a fresh empty context anyway -- so the scope has to be here,
    # around the staging below, not around the task that runs it.
    with origin_scope(Origin(OriginKind.SCHEDULE)):
        async with uow_scope(uow_factory) as uow:
            timers: list[ClaimedTimer] = []
            for claim_timers in timer_claimers:
                timers.extend(await claim_timers(uow.session, now=moment, limit=limit))
            for timer in timers:
                await emitter.stage_scheduled_job_event(
                    uow,
                    schedule_id=timer.timer_id,
                    user_id=timer.user_id,
                    payload=timer.payload,
                    scheduled_at=timer.fire_at,
                )

    return len(timers)


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
            await poll_due_schedules_once(uow_factory, timer_claimers=timer_claimers)
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
