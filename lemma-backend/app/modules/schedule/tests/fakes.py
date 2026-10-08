"""Fakes for the schedule ports, each implementing the port it stands for."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.core.origin import OriginKind, current_origin
from app.modules.schedule.domain.interfaces import (
    ScheduleEventPublisher,
    ScheduleFilterTaskQueue,
)
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleFireStatus


@dataclass(frozen=True, slots=True)
class QueuedFilter:
    schedule_id: UUID
    payload: dict[str, Any]
    metadata: dict[str, Any]
    source_event_id: str
    user_id: UUID | None
    #: The origin the enqueuing code ran under, which is the one the job
    #: inherits when it runs.
    origin: OriginKind | None


class RecordingFilterTaskQueue(ScheduleFilterTaskQueue):
    """Keeps what was queued instead of queueing it."""

    def __init__(self, journal: list[str] | None = None) -> None:
        self.queued: list[QueuedFilter] = []
        self._journal = journal

    async def enqueue(
        self,
        *,
        schedule_id: UUID,
        payload: dict[str, Any],
        metadata: dict[str, Any],
        source_event_id: str,
        user_id: UUID | None = None,
    ) -> None:
        origin = current_origin()
        self.queued.append(
            QueuedFilter(
                schedule_id=schedule_id,
                payload=payload,
                metadata=metadata,
                source_event_id=source_event_id,
                user_id=user_id,
                origin=origin.kind if origin is not None else None,
            )
        )
        if self._journal is not None:
            self._journal.append("enqueue")


@dataclass(frozen=True, slots=True)
class PublishedFire:
    schedule_id: UUID
    user_id: UUID
    source_event_id: str
    llm_output: dict[str, Any] | None


class RecordingScheduleEventPublisher(ScheduleEventPublisher):
    def __init__(self) -> None:
        self.fired: list[PublishedFire] = []

    async def publish_schedule_fired(
        self,
        schedule: ScheduleEntity,
        payload: dict[str, Any],
        source_event_id: str,
        user_id: UUID,
        metadata: dict[str, Any] | None = None,
        llm_output: dict[str, Any] | None = None,
    ) -> None:
        self.fired.append(
            PublishedFire(
                schedule_id=schedule.id,
                user_id=user_id,
                source_event_id=source_event_id,
                llm_output=llm_output,
            )
        )


class ScriptedScheduleFilter:
    """A `ScheduleEventFilter` that answers as told, or raises what it is given."""

    def __init__(
        self, *, proceed: bool = True, failure: Exception | None = None
    ) -> None:
        self._proceed = proceed
        self._failure = failure
        self.evaluated: list[dict[str, Any]] = []

    async def filter_event(
        self,
        *,
        instruction: str,
        output_schema: dict[str, Any] | None,
        event_payload: dict[str, Any],
        schedule: ScheduleEntity,
    ) -> tuple[bool, dict[str, Any] | None]:
        self.evaluated.append(event_payload)
        if self._failure is not None:
            raise self._failure
        if not self._proceed:
            return False, None
        return True, {"should_proceed": True}


@dataclass(frozen=True, slots=True)
class RecordedFire:
    schedule_id: UUID
    status: ScheduleFireStatus
    error: str | None


@dataclass
class InMemoryFilterTaskStore:
    """A `FilterTaskStore` over a dict, keeping every write it is asked for."""

    schedules: dict[UUID, ScheduleEntity] = field(default_factory=dict)
    fires: list[RecordedFire] = field(default_factory=list)
    pre_dispatch_failures: list[str] = field(default_factory=list)

    async def get_schedule(self, schedule_id: UUID) -> ScheduleEntity | None:
        return self.schedules.get(schedule_id)

    async def record_fire(
        self,
        schedule_id: UUID,
        *,
        status: ScheduleFireStatus,
        error: str | None = None,
    ) -> None:
        self.fires.append(RecordedFire(schedule_id, status, error))

    async def record_pre_dispatch_failure(
        self, schedule: ScheduleEntity, *, source_event_id: str, error_type: str
    ) -> bool:
        self.pre_dispatch_failures.append(error_type)
        return True
