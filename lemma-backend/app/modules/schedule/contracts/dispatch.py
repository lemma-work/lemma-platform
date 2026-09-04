"""What a target module does when a schedule fires at it.

Four operations, not the three classes `app/composition/workflow_schedule_runtime.py`
published. `workflow` was constructing `ScheduleRepository`, `ScheduleRunRepository`
and `ScheduleRunOutcomeService` itself, which made a repository's constructor part
of another module's build and left the caller to know that a dead-letter has to be
counted on the breaker in the same transaction that dead-lettered it.

A submodule for the same reason as its siblings here and in `connectors`: these
reach the model layer, and `contracts/__init__` is imported by anything that
wants any contract at all.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleFireStatus,
    ScheduleRunEntity,
    ScheduleRunStatus,
)
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.repositories.schedule_run_repository import (
    ScheduleRunRepository,
)
from app.modules.schedule.services.run_outcome_service import ScheduleRunOutcomeService


async def get_schedule(uow, schedule_id: UUID) -> ScheduleEntity | None:
    """The schedule a fire names, or ``None`` if it has since been deleted."""
    return await ScheduleRepository(uow).get(schedule_id)


async def claim_schedule_run(
    uow,
    *,
    schedule_id: UUID,
    user_id: UUID,
    source_event_id: str,
    target_kind: str,
    payload: dict[str, object],
    metadata: dict[str, object] | None,
    llm_output: dict[str, object] | None,
    source_occurred_at: datetime | None = None,
) -> ScheduleRunEntity | None:
    """Claim this fire, or ``None`` when another consumer already has it.

    The claim is the idempotency boundary: `source_event_id` is what makes a
    redelivered event a no-op rather than a second run.
    """
    return await ScheduleRunRepository(uow).claim(
        schedule_id=schedule_id,
        user_id=user_id,
        source_event_id=source_event_id,
        target_kind=target_kind,
        payload=payload,
        metadata=metadata,
        llm_output=llm_output,
        source_occurred_at=source_occurred_at,
    )


async def mark_run_dispatched(uow, run_id: UUID) -> None:
    """Mark launch complete unless a synchronous target outcome won the race."""
    await ScheduleRunRepository(uow).mark_dispatched(run_id)


async def mark_run_failed(uow, run_id: UUID, exc: Exception) -> ScheduleRunStatus:
    """Record a launch that raised, returning the run's resulting status."""
    return await ScheduleRunRepository(uow).mark_failed(run_id, exc)


async def record_fire(
    uow,
    schedule_id: UUID,
    *,
    status: ScheduleFireStatus,
    run_id: str | None = None,
    error: str | None = None,
) -> None:
    """Stamp the outcome of one fire onto the schedule."""
    await ScheduleRepository(uow).record_fire(
        schedule_id, status=status, run_id=run_id, error=error
    )


async def record_pre_dispatch_failure(
    uow, schedule: ScheduleEntity, *, source_event_id: str, error_type: str
) -> bool:
    """Record a fire that never reached its target, and count it on the breaker."""
    return await ScheduleRunOutcomeService(uow).record_pre_dispatch_failure(
        schedule, source_event_id=source_event_id, error_type=error_type
    )


async def record_dispatch_dead_letter(uow, schedule: ScheduleEntity) -> None:
    """Count a delivery failure in the transaction that first dead-lettered it."""
    await ScheduleRunOutcomeService(uow).record_dispatch_dead_letter(schedule)


__all__ = [
    "claim_schedule_run",
    "get_schedule",
    "mark_run_dispatched",
    "mark_run_failed",
    "record_dispatch_dead_letter",
    "record_fire",
    "record_pre_dispatch_failure",
]
