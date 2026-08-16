"""Function domain events."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.core.domain.events import DomainEvent
from app.modules.function.domain.types import JsonObject

FUNCTION_EVENTS_STREAM = "function.events"
FUNCTION_RUN_EVENTS_STREAM = "function_run_events"


class FunctionCreatedEvent(DomainEvent):
    event_type: str = "function.created"
    function_id: UUID
    pod_id: UUID
    #: Whoever created it. The row has always carried this; the event did not,
    #: so every function landed in analytics attributed to the machine actor.
    user_id: UUID | None = None

    @classmethod
    def stream_name(cls) -> str:
        return FUNCTION_EVENTS_STREAM


class FunctionUpdatedEvent(DomainEvent):
    event_type: str = "function.updated"
    function_id: UUID
    pod_id: UUID

    @classmethod
    def stream_name(cls) -> str:
        return FUNCTION_EVENTS_STREAM


class FunctionDeletedEvent(DomainEvent):
    event_type: str = "function.deleted"
    function_id: UUID
    pod_id: UUID

    @classmethod
    def stream_name(cls) -> str:
        return FUNCTION_EVENTS_STREAM


class FunctionRunStartedEvent(DomainEvent):
    event_type: str = "function.run.started"
    run_id: UUID
    function_id: UUID
    started_at: datetime
    user_email: str | None = None

    @classmethod
    def stream_name(cls) -> str:
        return FUNCTION_RUN_EVENTS_STREAM


class FunctionRunLogsUpdatedEvent(DomainEvent):
    event_type: str = "function.run.logs_updated"
    run_id: UUID
    function_id: UUID
    logs: str | None = None

    @classmethod
    def stream_name(cls) -> str:
        return FUNCTION_RUN_EVENTS_STREAM


class FunctionRunCompletedEvent(DomainEvent):
    event_type: str = "function.run.completed"
    run_id: UUID
    function_id: UUID
    output_data: JsonObject | None = None
    logs: str | None = None
    completed_at: datetime

    @classmethod
    def stream_name(cls) -> str:
        return FUNCTION_RUN_EVENTS_STREAM


class FunctionRunFailedEvent(DomainEvent):
    event_type: str = "function.run.failed"
    run_id: UUID
    function_id: UUID
    error: str | None = None
    logs: str | None = None
    completed_at: datetime

    @classmethod
    def stream_name(cls) -> str:
        return FUNCTION_RUN_EVENTS_STREAM
