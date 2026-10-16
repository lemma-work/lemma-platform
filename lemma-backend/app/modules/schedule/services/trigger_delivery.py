"""One page of record changes, delivered to one DATASTORE trigger.

The shared half of DATASTORE delivery: whatever reads a pod's changes (the
change log's ``changes.deliver``) hands each trigger a page of changes already
narrowed to its table and past its cursor, and this decides, in the caller's
main-database transaction, what the trigger does with each:

* not matching (operation, ``when``, an outside row the trigger does not take)
  → nothing;
* a ``bulk`` or ``import`` write → the trigger's write mode for it
  (``bulk_writes`` / ``import_writes``, else the pod's
  ``triggers.bulk_writes_default`` for bulk, else ``skip``):
    - ``skip`` → one aggregated ``trigger_skips`` row per page and reason;
    - ``each`` → fired like a normal write;
    - ``batch`` → one planned batch per page, fired once with the changes as a
      list (``batch:<schedule>:<first>``);
* a normal write → one fire per change (``chg:<xid>:<seq>``).

Fires stop at the daily ceiling: the trigger pauses and the rest of the page's
matches are recorded as ``fire_cap`` skips. Every write is keyed (run source,
skip page, planned batch), so redelivering a page after a crash changes
nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import JsonValue

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter
from app.core.domain.record_conditions import evaluate_match_conditions
from app.modules.schedule.domain.schedule import (
    DatastoreScheduleConfig,
    ScheduleEntity,
    TriggerWriteMode,
)
from app.modules.schedule.domain.value_objects import DatastoreOperation
from app.modules.schedule.repositories.trigger_records import TriggerRecords
from app.modules.schedule.services.fire_cap import (
    FIRE_CAP,
    fires_left_today,
    pause_at_fire_cap,
)
from app.modules.schedule.services.trigger_fire import (
    FireOutcome,
    TriggerEvent,
    fire_trigger,
)

BULK_WRITE = "bulk_write"
IMPORT_WRITE = "import_write"
#: The trigger fell further behind its table than the change log keeps a
#: subscriber waiting, and was moved past what it had not read.
LAG_EXCEEDED = "lag_exceeded"


class WriteIntent(StrEnum):
    NORMAL = "normal"
    BULK = "bulk"
    IMPORT = "import"


@dataclass(frozen=True, slots=True, order=True)
class ChangePosition:
    """Where a change sits in its pod's log: ``(xid, seq)``, totally ordered."""

    xid: int
    seq: int

    def __str__(self) -> str:
        return f"{self.xid}:{self.seq}"


@dataclass(frozen=True, slots=True)
class TriggerChange:
    position: ChangePosition
    operation: DatastoreOperation
    record_id: str
    intent: WriteIntent = WriteIntent.NORMAL
    #: The row after the write (before it, for a delete).
    row: Mapping[str, JsonValue] = field(default_factory=dict)
    changed: Sequence[str] | None = None
    previous: Mapping[str, JsonValue] | None = None
    owner_user_id: UUID | None = None
    occurred_at: datetime | None = None
    from_outside: bool = False
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    #: For a change converted from the old event path at cutover: the event's
    #: id, which the old consumer fired under, so a change it already fired is
    #: not fired again.
    legacy_event_id: str | None = None

    @property
    def source_event_id(self) -> str:
        return self.legacy_event_id or f"chg:{self.position}"


@dataclass(slots=True)
class PageResult:
    fired: int = 0
    filtering: int = 0
    batches: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    paused: bool = False


def write_mode(
    schedule_config: DatastoreScheduleConfig,
    intent: WriteIntent,
    *,
    pod_bulk_default: TriggerWriteMode | None,
) -> TriggerWriteMode:
    """What this trigger does with a change of ``intent``."""
    if intent is WriteIntent.BULK:
        return schedule_config.bulk_writes or pod_bulk_default or TriggerWriteMode.SKIP
    if intent is WriteIntent.IMPORT:
        return schedule_config.import_writes or TriggerWriteMode.SKIP
    return TriggerWriteMode.EACH


def matches(
    schedule: ScheduleEntity, config: DatastoreScheduleConfig, change: TriggerChange
) -> bool:
    if change.from_outside and not schedule.include_outside_rows:
        return False
    if config.operations and change.operation not in config.operations:
        return False
    if not config.when:
        return True
    return evaluate_match_conditions(
        config.when,
        operation=change.operation,
        payload=dict(change.row),
        changed=list(change.changed) if change.changed is not None else None,
        previous=dict(change.previous) if change.previous is not None else None,
    )


@dataclass(slots=True)
class _Sorted:
    skips: dict[str, list[TriggerChange]] = field(default_factory=dict)
    each: list[TriggerChange] = field(default_factory=list)
    batch: list[TriggerChange] = field(default_factory=list)


def _sort(
    schedule: ScheduleEntity,
    config: DatastoreScheduleConfig,
    changes: Sequence[TriggerChange],
    pod_bulk_default: TriggerWriteMode | None,
) -> _Sorted:
    """The page's matching changes, by what the trigger does with each."""
    sorted_page = _Sorted()
    for change in sorted(changes, key=lambda c: c.position):
        if not matches(schedule, config, change):
            continue
        mode = write_mode(config, change.intent, pod_bulk_default=pod_bulk_default)
        if mode is TriggerWriteMode.SKIP:
            reason = BULK_WRITE if change.intent is WriteIntent.BULK else IMPORT_WRITE
            sorted_page.skips.setdefault(reason, []).append(change)
        elif mode is TriggerWriteMode.BATCH:
            sorted_page.batch.append(change)
        else:
            sorted_page.each.append(change)
    return sorted_page


class _Page:
    """Fires one trigger's page against its remaining daily budget."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        schedule: ScheduleEntity,
        writer: WorkWriter,
        budget: int | None,
    ) -> None:
        self.uow = uow
        self.schedule = schedule
        self.writer = writer
        self.budget = budget
        self.result = PageResult()
        self.capped: list[TriggerChange] = []

    async def at_ceiling(self, first: TriggerChange) -> bool:
        """Past the ceiling the trigger pauses once; everything after is capped."""
        if self.result.paused:
            return True
        if self.budget is None or self.budget > 0:
            return False
        # Keyed as the page's own fire_cap row is (the change's position), so
        # that row rewrites this one instead of appearing beside it.
        await pause_at_fire_cap(
            self.uow,
            self.schedule,
            source_event_id=str(first.position),
            work=self.writer,
        )
        self.result.paused = True
        return True

    async def fire_each(self, changes: Sequence[TriggerChange]) -> None:
        for change in changes:
            if await self.at_ceiling(change):
                self.capped.append(change)
                continue
            outcome = await fire_trigger(
                self.uow,
                self.schedule,
                _event(change),
                work=self.writer,
                cap_checked=True,
            )
            if outcome is FireOutcome.STARTED:
                self.result.fired += 1
                self.budget = None if self.budget is None else self.budget - 1
            elif outcome is FireOutcome.FILTERING:
                self.result.filtering += 1


async def deliver_page(
    uow: SqlAlchemyUnitOfWork,
    schedule: ScheduleEntity,
    changes: Sequence[TriggerChange],
    *,
    pod_bulk_default: TriggerWriteMode | None = None,
    work: WorkWriter | None = None,
) -> PageResult:
    config = schedule.datastore_config
    if config is None or not schedule.is_active:
        return PageResult()
    records = TriggerRecords(uow.session)
    sorted_page = _sort(schedule, config, changes, pod_bulk_default)
    page = _Page(
        uow, schedule, work or WorkWriter.of(uow), await fires_left_today(uow, schedule)
    )
    for reason, skipped in sorted_page.skips.items():
        await _record(records, schedule.id, reason, skipped, page.result)
    await page.fire_each(sorted_page.each)
    if sorted_page.batch:
        if await page.at_ceiling(sorted_page.batch[0]):
            page.capped.extend(sorted_page.batch)
        else:
            await _fire_batch(
                uow, records, schedule, sorted_page.batch, page.writer, page.result
            )
    if page.capped:
        # The pause recorded the change that hit the ceiling; this rewrites
        # that row with the rest of the page the trigger would have fired.
        await _record(records, schedule.id, FIRE_CAP, page.capped, page.result)
    return page.result


async def _fire_batch(
    uow: SqlAlchemyUnitOfWork,
    records: TriggerRecords,
    schedule: ScheduleEntity,
    batch: Sequence[TriggerChange],
    writer: WorkWriter,
    result: PageResult,
) -> None:
    # A replayed page can be longer than the one first planned (changes that
    # became readable since), never different before its end. The planned
    # window is fired as it was cut; what follows it is planned as the next
    # batch, so nothing past a planned ``last`` is dropped.
    remaining = list(batch)
    while remaining:
        first, last = remaining[0].position, remaining[-1].position
        planned = await records.plan_batch(
            schedule.id, first=str(first), last=str(last), count=len(remaining)
        )
        window = [c for c in remaining if _at_or_before(c.position, planned.last)]
        remaining = [
            c for c in remaining if not _at_or_before(c.position, planned.last)
        ]
        outcome = await fire_trigger(
            uow,
            schedule,
            TriggerEvent(
                source_event_id=f"batch:{schedule.id}:{first}",
                payload={
                    "changes": [
                        {
                            "operation": c.operation.value,
                            "record_id": c.record_id,
                            "row": dict(c.row),
                        }
                        for c in window
                    ]
                },
                metadata={
                    "batch_first": str(first),
                    "batch_last": planned.last,
                    "count": len(window),
                },
                source_occurred_at=window[-1].occurred_at if window else None,
            ),
            work=writer,
            cap_checked=True,
        )
        if outcome in (FireOutcome.STARTED, FireOutcome.FILTERING):
            result.batches += 1


def _at_or_before(position: ChangePosition, last: str) -> bool:
    xid, _, seq = last.partition(":")
    return position <= ChangePosition(int(xid), int(seq))


async def _record(
    records: TriggerRecords,
    schedule_id: UUID,
    reason: str,
    changes: Sequence[TriggerChange],
    result: PageResult,
) -> None:
    await records.record_skips(
        schedule_id,
        reason=reason,
        first=str(changes[0].position),
        last=str(changes[-1].position),
        count=len(changes),
    )
    result.skipped[reason] = result.skipped.get(reason, 0) + len(changes)


def _event(change: TriggerChange) -> TriggerEvent:
    return TriggerEvent(
        source_event_id=change.source_event_id,
        payload=dict(change.row),
        metadata={
            **dict(change.metadata),
            "record_id": change.record_id,
            "operation": change.operation.value,
            "changed": list(change.changed or []),
            "previous": dict(change.previous or {}),
        },
        user_id=change.owner_user_id,
        source_occurred_at=change.occurred_at,
    )


__all__ = [
    "BULK_WRITE",
    "IMPORT_WRITE",
    "LAG_EXCEEDED",
    "ChangePosition",
    "PageResult",
    "TriggerChange",
    "WriteIntent",
    "deliver_page",
    "matches",
    "write_mode",
]
