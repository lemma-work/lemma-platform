"""Function domain events."""

from __future__ import annotations

from uuid import UUID

from app.core.domain.events import DomainEvent

FUNCTION_EVENTS_STREAM = "function.events"


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
