"""App domain events published to the ``app_events`` Redis stream."""

from __future__ import annotations

from uuid import UUID

from app.core.domain.events import DomainEvent

APP_EVENTS_STREAM = "app_events"


class AppDomainEvent(DomainEvent):
    @classmethod
    def stream_name(cls) -> str:
        return APP_EVENTS_STREAM


class AppCreatedEvent(AppDomainEvent):
    event_type: str = "app.created"
    app_id: UUID
    pod_id: UUID
    user_id: UUID | None = None


class AppPublishedEvent(AppDomainEvent):
    """An app reached READY for the first time.

    Only on the transition. A re-upload of an already-published app is a new
    release, not a new publish, and counting those would make "apps published"
    track deploy frequency instead of how many pods have shipped something.
    """

    event_type: str = "app.published"
    app_id: UUID
    pod_id: UUID
    user_id: UUID | None = None
