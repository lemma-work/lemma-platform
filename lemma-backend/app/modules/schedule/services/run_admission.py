"""Holding a schedule to its ``max_concurrent_runs``.

A schedule that fires on every row written or every webhook delivered can start
runs faster than they finish -- and an agent run is minutes of model work, so a
burst becomes a pile of concurrent conversations nobody asked for all at once.
A workflow target has its own cap, which queues; this one belongs to the
schedule, works for either target, and skips instead: a fire that finds the
schedule full is recorded as skipped, where the owner can see it, and starts
nothing.

Skipped rather than queued because a schedule has nowhere durable to park a
fire -- the event is gone once handled -- and because for most capped schedules
"one at a time, and drop what arrives meanwhile" is the point: a sync that is
already running does not need a second one queued behind it.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.schedule.domain.schedule import ScheduleEntity, schedule_run_cap
from app.modules.schedule.repositories.schedule_run_repository import (
    ScheduleRunRepository,
)
from app.modules.schedule.services.run_outcome_service import (
    ScheduleRunOutcomeService,
)

logger = get_logger(__name__)

#: Why a fire was skipped, as the skipped run's metadata says it.
SKIPPED_AT_CAPACITY = "max_concurrent_runs"


async def admit_fire(
    uow: SqlAlchemyUnitOfWork,
    schedule: ScheduleEntity,
    *,
    source_event_id: str,
    user_id: UUID,
    metadata: Mapping[str, object] | None,
    claimed: bool = False,
) -> bool:
    """Whether this fire may claim a run. When it may not, it is recorded.

    ``claimed``: the fire's row already exists (a filter verdict promoting its
    own FILTERING row), so it is counted rather than waved through, and a
    refusal is left for the caller to settle on that row.

    Call before claiming, in the transaction that claims: the lock taken here
    is held until that claim commits, which is what makes the count honest.

    A fire that already has a run is admitted without counting. It was
    admitted once; this is its redelivery or retry, and turning it away now
    would strand the run it already claimed.
    """
    try:
        cap = schedule_run_cap(schedule.config)
    except ValueError:
        # Saved before the cap was validated on write. Refusing every fire
        # over it would stop a schedule that ran fine until now.
        logger.warning(
            "schedule.run_cap.invalid_config.degraded",
            schedule_id=str(schedule.id),
        )
        return True
    if cap is None:
        return True
    runs = ScheduleRunRepository(uow)
    await runs.lock_admission(schedule.id)
    if not claimed and await runs.has_run_for_event(
        schedule_id=schedule.id, source_event_id=source_event_id
    ):
        return True
    in_flight = await runs.count_in_flight(
        schedule.id, excluding_source_event_id=source_event_id, limit=cap
    )
    if in_flight < cap:
        return True
    if claimed:
        return False
    await ScheduleRunOutcomeService(uow).record_filtered(
        schedule,
        source_event_id=source_event_id,
        user_id=user_id,
        metadata={
            **(metadata or {}),
            "skipped_because": SKIPPED_AT_CAPACITY,
            "max_concurrent_runs": cap,
        },
        llm_output=None,
    )
    logger.info(
        "schedule.run_cap.fire_skipped",
        schedule_id=str(schedule.id),
        max_concurrent_runs=cap,
    )
    return False
