"""Pod module domain events."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.core.domain.events import DomainEvent


POD_EVENTS_STREAM = "pod_events"


class PodCreatedEvent(DomainEvent):
    event_type: str = "pod.created"
    pod_id: UUID
    organization_id: UUID
    creator_id: UUID
    name: str
    #: ``PodCreationSource``: asked for, or provisioned. A string so an older
    #: replica's event without it still validates.
    source: str | None = None

    @classmethod
    def stream_name(cls) -> str:
        return POD_EVENTS_STREAM


class PodMemberAddedEvent(DomainEvent):
    event_type: str = "pod.member.added"
    pod_id: UUID
    user_id: UUID
    role: str
    email: str
    first_name: str | None = None
    last_name: str | None = None

    @classmethod
    def stream_name(cls) -> str:
        return POD_EVENTS_STREAM


class PodMemberRemovedEvent(DomainEvent):
    event_type: str = "pod.member.removed"
    pod_id: UUID
    user_id: UUID

    @classmethod
    def stream_name(cls) -> str:
        return POD_EVENTS_STREAM


class PodDeletedEvent(DomainEvent):
    event_type: str = "pod.deleted"
    pod_id: UUID
    organization_id: UUID
    #: Who deleted it -- read from the request's authorization context, not from
    #: the pod's own ``user_id``: an admin can delete a pod somebody else built,
    #: and attributing that to the creator would be a false statement rather than
    #: a missing one.
    deleted_by_user_id: UUID | None = None
    #: When the pod was made, so how long it lived is known without reading a
    #: row the deletion is about to make irrelevant.
    pod_created_at: datetime | None = None

    @classmethod
    def stream_name(cls) -> str:
        return POD_EVENTS_STREAM


class PodJoinRequestedEvent(DomainEvent):
    event_type: str = "pod.join_request.requested"
    pod_id: UUID
    organization_id: UUID
    requester_user_id: UUID
    join_request_id: UUID

    @classmethod
    def stream_name(cls) -> str:
        return POD_EVENTS_STREAM


class PodEvents:
    STREAM = POD_EVENTS_STREAM
