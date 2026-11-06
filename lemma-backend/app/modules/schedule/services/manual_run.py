"""A person starting one run of a schedule now (``schedule.run.create``).

The run takes the path a timer's firing takes, ``fire_trigger``, in the
request's transaction: the daily ceiling, ``max_concurrent_runs``, the reserved
run and the ``schedule.start_target`` item that starts its target. So a run
started by hand counts toward the limits a timer's does, is stopped by them the
same way, and is started by the same job -- which applies whatever the target's
own start applies, such as the pod's spend and the target's permissions as of
then.

Where it differs, it is because a person asked:

* it runs as that person, never as the schedule's owner -- like a retried run,
  so nobody with a schedule in reach borrows its owner's access;
* its filter is not asked (``TriggerEvent.started_by_user_id``);
* a limit that stops it is answered as a refusal. What the fire recorded there
  -- the skipped run, or the ceiling's pause and the owner's notice -- is
  committed first, as it is for any fire.

Only a TIME schedule can be run this way. A WEBHOOK or DATASTORE schedule's run
carries the delivery or the changed record that fired it, and a run started by
hand has neither to give it.
"""

from __future__ import annotations

from uuid import UUID, uuid7

from app.core.authorization.context import Context, ResourceRef, ResourceType
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.schedule.domain.errors import (
    ScheduleNotFoundError,
    ScheduleNotRunnableNowError,
)
from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleRunEntity,
    ScheduleType,
)
from app.modules.schedule.repositories.schedule_run_repository import (
    ScheduleRunRepository,
)
from app.modules.schedule.services.schedule_target_policy import agent_execute_ref
from app.modules.schedule.services.trigger_fire import (
    FireOutcome,
    TriggerEvent,
    fire_trigger,
)
from app.modules.schedule.work.kinds import time_event_payload

#: A run started by hand is keyed ``manual:<uuid>``: each request is its own
#: event, never a repeat of an earlier one.
MANUAL_SOURCE_PREFIX = "manual:"
#: What the run's metadata says, and so what its target reads, about who asked.
MANUAL = "manual"
STARTED_BY_USER_ID = "started_by_user_id"

#: The error codes a refused run by hand answers with.
CODE_NEEDS_EVENT = "SCHEDULE_RUN_NEEDS_EVENT"
CODE_TARGET_MISSING = "SCHEDULE_TARGET_MISSING"
CODE_INACTIVE = "SCHEDULE_INACTIVE"
CODE_AT_CAPACITY = "SCHEDULE_AT_CAPACITY"
CODE_FIRE_CAP_REACHED = "SCHEDULE_FIRE_CAP_REACHED"

_WHAT_FIRES_IT: dict[ScheduleType, str] = {
    ScheduleType.WEBHOOK: "the webhook delivery",
    ScheduleType.DATASTORE: "the changed record",
}


def refuse_manual_run(schedule: ScheduleEntity) -> None:
    """Raise why ``schedule`` cannot be run by hand now; return if it can."""
    if schedule.schedule_type != ScheduleType.TIME:
        raise ScheduleNotRunnableNowError(
            f"Only a time schedule can be run now. A {schedule.schedule_type.value} "
            "schedule's run carries "
            f"{_WHAT_FIRES_IT.get(schedule.schedule_type, 'the event')} "
            "that fired it, and a run started by hand has none to give it. To "
            "try its target, run the agent or workflow directly.",
            code=CODE_NEEDS_EVENT,
            status_code=422,
        )
    if not schedule.has_target:
        raise ScheduleNotRunnableNowError(
            "This schedule's agent or workflow was deleted, so there is nothing "
            "to run. Point it at a target first.",
            code=CODE_TARGET_MISSING,
        )
    if not schedule.is_active:
        # A pause stops every run, not only the timer's: the platform's pauses
        # (a failure streak, the daily ceiling) are safety stops a run by hand
        # must not walk around, and the start job skips an inactive
        # schedule's run anyway.
        why = f" ({schedule.paused_reason})" if schedule.paused_reason else ""
        raise ScheduleNotRunnableNowError(
            f"This schedule is paused{why}. Resume it before running it now.",
            code=CODE_INACTIVE,
        )


def manual_trigger_event(schedule: ScheduleEntity, *, person: UUID) -> TriggerEvent:
    """The event a run by hand fires with: what the timer would carry, and who asked."""
    return TriggerEvent(
        source_event_id=f"{MANUAL_SOURCE_PREFIX}{uuid7()}",
        payload=time_event_payload(schedule.config),
        metadata={MANUAL: True, STARTED_BY_USER_ID: str(person)},
        started_by_user_id=person,
    )


async def require_may_run_target(
    ctx: Context, schedule: ScheduleEntity, *, pod_id: UUID
) -> None:
    """The person asking runs the target, so they must be allowed to.

    Asked here for a clear refusal before anything is written; the start job
    asks again as of when it starts, and skips the run if the answer changed.
    """
    if schedule.agent_id is not None:
        await ctx.require(
            Permissions.AGENT_EXECUTE,
            agent_execute_ref(schedule.agent_id, pod_id=pod_id),
        )
    if schedule.workflow_id is not None:
        await ctx.require(
            Permissions.WORKFLOW_EXECUTE,
            ResourceRef(
                resource_type=ResourceType.WORKFLOW,
                resource_id=schedule.workflow_id,
                pod_id=pod_id,
            ),
        )


async def fire_manual_run(
    uow: SqlAlchemyUnitOfWork, schedule: ScheduleEntity, *, person: UUID
) -> ScheduleRunEntity:
    """Fire ``schedule`` once, now, as ``person``; the run it reserved.

    For a schedule ``refuse_manual_run`` let through. Raises when a limit
    stops the fire, after committing what the fire recorded at it.
    """
    event = manual_trigger_event(schedule, person=person)
    outcome = await fire_trigger(uow, schedule, event)
    if outcome is FireOutcome.AT_CAPACITY:
        await uow.commit()
        raise ScheduleNotRunnableNowError(
            "This schedule already has as many runs in flight as its "
            "max_concurrent_runs allows, so this one was skipped and recorded "
            "as skipped. Start it again once one finishes.",
            code=CODE_AT_CAPACITY,
        )
    if outcome is FireOutcome.FIRE_CAP:
        await uow.commit()
        raise ScheduleNotRunnableNowError(
            "This schedule has reached its daily ceiling on runs, so it has "
            "been paused and its owner told, as any trigger past the ceiling "
            "is. Resume it once its runs from the last day have aged out.",
            code=CODE_FIRE_CAP_REACHED,
        )
    run = await ScheduleRunRepository(uow).get_for_event(
        schedule_id=schedule.id, source_event_id=event.source_event_id
    )
    if outcome is not FireOutcome.STARTED or run is None:
        # The event is new and the filter is not asked, so the only fire that
        # leaves no run is one whose schedule was deleted under it.
        raise ScheduleNotFoundError()
    return run


__all__ = [
    "CODE_AT_CAPACITY",
    "CODE_FIRE_CAP_REACHED",
    "CODE_INACTIVE",
    "CODE_NEEDS_EVENT",
    "CODE_TARGET_MISSING",
    "MANUAL",
    "MANUAL_SOURCE_PREFIX",
    "STARTED_BY_USER_ID",
    "fire_manual_run",
    "manual_trigger_event",
    "refuse_manual_run",
    "require_may_run_target",
]
