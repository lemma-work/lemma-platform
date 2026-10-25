"""One fire, for any trigger source, in the caller's transaction.

TIME occurrences, webhook deliveries, table changes and filter verdicts all
end here, so every source gets the same rules in the same order:

1. a schedule with an LLM filter gets a ``schedule.filter`` item instead
   (the filter job comes back through ``start_after_filter``);
2. the daily ceiling (``fire_cap``) -- past it the trigger pauses itself;
3. ``max_concurrent_runs`` -- a full schedule records the fire as skipped;
4. the run is reserved, unique on ``(schedule_id, source_event_id)``, so a
   repeat of the same event is a no-op;
5. ``schedule.start_target`` is written beside it, keyed on the run.

Everything is a database write on the caller's open transaction: the fire and
the work it causes commit together or not at all. Nothing here does I/O, so a
FAST handler may call it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue
from pydantic_core import to_jsonable_python

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter
from app.core.log.log import get_logger
from app.modules.schedule.domain.schedule import ScheduleEntity, runs_as_row_owner
from app.modules.schedule.repositories.fire_ledger import FireLedger
from app.modules.schedule.services.fire_cap import at_daily_ceiling, pause_at_fire_cap
from app.modules.schedule.services.run_admission import admit_fire
from app.modules.schedule.work import registration as _registration  # noqa: F401  registers the kinds this writes
from app.modules.schedule.work.kinds import (
    SCHEDULE_FILTER,
    SCHEDULE_START,
    FilterEvent,
    StartTarget,
    schedule_key,
    schedule_namespace,
)

from app.core.observability.product_metrics import record_schedule_run

logger = get_logger(__name__)


class FireOutcome(StrEnum):
    STARTED = "started"
    FILTERING = "filtering"
    DUPLICATE = "duplicate"
    AT_CAPACITY = "at_capacity"
    FIRE_CAP = "fire_cap"
    INACTIVE = "inactive"


@dataclass(frozen=True, slots=True)
class TriggerEvent:
    """What fired: the source's stable id for it, and what it carried."""

    source_event_id: str
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    #: The person who owns the changed row, for a table change that has one.
    row_owner_user_id: UUID | None = None
    source_occurred_at: datetime | None = None


def run_person(schedule: ScheduleEntity, row_owner_user_id: UUID | None) -> UUID | None:
    """Whose authority a fire runs with.

    A table change on an RLS table runs as the person who owns the row,
    unless the schedule says to run as its owner: the run then sees what
    that person sees, bounded by the target, as if they had asked the agent
    themselves -- running it as the schedule's owner instead handed every
    member who can write a row the owner's access, through the row's text.
    The other direction, a schedule's author borrowing whoever writes, is
    closed where the schedule is made: running as row owners takes edit
    access to the target (``schedule_ownership``). Everything else -- time,
    webhooks, rows nobody in the pod owns, batches -- runs as the owner.
    """
    if row_owner_user_id is not None and runs_as_row_owner(schedule):
        return row_owner_user_id
    return schedule.user_id


class FireTrigger(Protocol):
    """``fire_trigger``'s shape, for callers that take it as a seam."""

    async def __call__(
        self, uow: SqlAlchemyUnitOfWork, schedule: ScheduleEntity, event: TriggerEvent
    ) -> FireOutcome: ...


def jsonable(value: Mapping[str, object] | None) -> dict[str, JsonValue]:
    """An event body as plain JSON: work payloads and run rows are jsonb."""
    converted = to_jsonable_python(dict(value or {}), fallback=str)
    return converted if isinstance(converted, dict) else {}


def target_kind_of(schedule: ScheduleEntity) -> str:
    return "WORKFLOW" if schedule.workflow_id is not None else "AGENT"


_RUN_OUTCOME_OF_FIRE = {
    FireOutcome.STARTED: "triggered",
    FireOutcome.FIRE_CAP: "skipped",
    FireOutcome.AT_CAPACITY: "skipped",
}


def _counted(schedule: ScheduleEntity, outcome: FireOutcome) -> FireOutcome:
    """Count a fire decision on ``lemma.schedule.runs``. A filtering hand-off
    is counted when the filter answers; a duplicate or an inactive schedule
    decided nothing."""
    counted = _RUN_OUTCOME_OF_FIRE.get(outcome)
    if counted is not None:
        record_schedule_run(schedule.schedule_type, counted)
    return outcome


async def fire_trigger(
    uow: SqlAlchemyUnitOfWork,
    schedule: ScheduleEntity,
    event: TriggerEvent,
    *,
    work: WorkWriter | None = None,
    cap_checked: bool = False,
) -> FireOutcome:
    return _counted(
        schedule,
        await _fire_trigger(uow, schedule, event, work=work, cap_checked=cap_checked),
    )


async def _fire_trigger(
    uow: SqlAlchemyUnitOfWork,
    schedule: ScheduleEntity,
    event: TriggerEvent,
    *,
    work: WorkWriter | None,
    cap_checked: bool,
) -> FireOutcome:
    """Fire ``schedule`` for ``event`` (or hand it to its filter).

    ``cap_checked``: the caller already holds a budget under the daily ceiling
    for this fire (a page of changes counts once, not per change).
    """
    writer = work or WorkWriter.of(uow)
    if not schedule.is_active:
        return FireOutcome.INACTIVE
    if schedule.filter_instruction:
        await writer.add(
            SCHEDULE_FILTER,
            schedule_namespace(schedule.pod_id),
            FilterEvent(
                schedule_id=schedule.id,
                source_event_id=event.source_event_id,
                payload=dict(event.payload),
                metadata=dict(event.metadata),
                row_owner_user_id=event.row_owner_user_id,
                source_occurred_at=event.source_occurred_at,
            ),
            dedup_key=f"filter:{schedule.id}:{event.source_event_id}",
            concurrency_key=schedule_key(schedule.id),
            rate_key=schedule_key(schedule.id),
        )
        return FireOutcome.FILTERING
    if not cap_checked and await at_daily_ceiling(uow, schedule):
        await pause_at_fire_cap(
            uow, schedule, source_event_id=event.source_event_id, work=writer
        )
        return FireOutcome.FIRE_CAP
    person = run_person(schedule, event.row_owner_user_id)
    if not await admit_fire(
        uow,
        schedule,
        source_event_id=event.source_event_id,
        user_id=person,
        metadata=event.metadata,
    ):
        return FireOutcome.AT_CAPACITY
    reservation = await FireLedger(uow.session).reserve(
        schedule_id=schedule.id,
        user_id=person,
        source_event_id=event.source_event_id,
        target_kind=target_kind_of(schedule),
        payload=event.payload,
        metadata=event.metadata,
        llm_output=None,
        source_occurred_at=event.source_occurred_at,
    )
    if reservation is None or not reservation.created:
        return FireOutcome.DUPLICATE
    await add_start_work(writer, schedule, reservation.run_id)
    return FireOutcome.STARTED


async def start_after_filter(
    uow: SqlAlchemyUnitOfWork,
    schedule: ScheduleEntity,
    *,
    run_id: UUID,
    source_event_id: str,
    llm_output: Mapping[str, JsonValue],
    metadata: Mapping[str, JsonValue],
    work: WorkWriter,
) -> FireOutcome:
    """The filter said yes for the FILTERING run ``run_id``: fire it, or skip it."""
    return _counted(
        schedule,
        await _start_after_filter(
            uow,
            schedule,
            run_id=run_id,
            source_event_id=source_event_id,
            llm_output=llm_output,
            metadata=metadata,
            work=work,
        ),
    )


async def _start_after_filter(
    uow: SqlAlchemyUnitOfWork,
    schedule: ScheduleEntity,
    *,
    run_id: UUID,
    source_event_id: str,
    llm_output: Mapping[str, JsonValue],
    metadata: Mapping[str, JsonValue],
    work: WorkWriter,
) -> FireOutcome:
    ledger = FireLedger(uow.session)
    if not schedule.is_active:
        await ledger.settle_skipped(
            run_id, llm_output=llm_output, metadata={"skipped_because": "inactive"}
        )
        return FireOutcome.INACTIVE
    if await at_daily_ceiling(uow, schedule):
        await ledger.settle_skipped(
            run_id, llm_output=llm_output, metadata={"skipped_because": "fire_cap"}
        )
        await pause_at_fire_cap(
            uow, schedule, source_event_id=source_event_id, work=work
        )
        return FireOutcome.FIRE_CAP
    if not await ledger.promote_filtering(run_id, llm_output=llm_output):
        return FireOutcome.DUPLICATE
    # Admitted after promotion: the cap counts this run's siblings, and a
    # FILTERING row is not one of them.
    if not await admit_fire(
        uow,
        schedule,
        source_event_id=source_event_id,
        user_id=schedule.user_id,
        metadata=metadata,
        claimed=True,
    ):
        await ledger.settle_skipped(
            run_id, metadata={"skipped_because": "max_concurrent_runs"}
        )
        return FireOutcome.AT_CAPACITY
    await add_start_work(work, schedule, run_id)
    return FireOutcome.STARTED


async def add_start_work(
    work: WorkWriter, schedule: ScheduleEntity, run_id: UUID
) -> None:
    await work.add(
        SCHEDULE_START,
        schedule_namespace(schedule.pod_id),
        StartTarget(schedule_run_id=run_id),
        dedup_key=f"start:{run_id}",
        rate_key=schedule_key(schedule.id),
    )


__all__ = [
    "FireOutcome",
    "FireTrigger",
    "TriggerEvent",
    "add_start_work",
    "fire_trigger",
    "jsonable",
    "start_after_filter",
    "target_kind_of",
]
