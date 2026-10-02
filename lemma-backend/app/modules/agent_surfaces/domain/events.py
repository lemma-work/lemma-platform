"""Surface domain events published to the ``surface_events`` Redis stream."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import JsonValue

from app.core.domain.events import DomainEvent
from app.modules.agent_surfaces.domain.notification import (
    NotificationEntity,
    NotificationOriginKind,
    NotificationStatus,
)


class SurfaceEvents:
    STREAM = "surface_events"


class SurfaceOnboardingReadyEvent(DomainEvent):
    event_type: str = "surface.onboarding.ready"
    pending_id: UUID

    @classmethod
    def stream_name(cls) -> str:
        return SurfaceEvents.STREAM


class SurfaceConnectedEvent(DomainEvent):
    """A surface was created for a pod.

    ``surface_type`` rides along because not every surface is somebody
    connecting one: a Resend mailbox is provisioned automatically for every agent
    at creation, so counting those as reach would make the number meaningless.
    The exclusion lives in the analytics consumer, not here -- a surface really
    was created, and the domain event should say so.
    """

    event_type: str = "surface.connected"
    surface_id: UUID
    pod_id: UUID
    platform: str
    agent_id: UUID | None = None
    #: Who connected it, from the request's authorization context. Absent for the
    #: mailbox provisioned automatically with an agent, which is the one case
    #: where nobody connected anything.
    connected_by_user_id: UUID | None = None

    @classmethod
    def stream_name(cls) -> str:
        return SurfaceEvents.STREAM


class NotificationSettledEvent(DomainEvent):
    """An asking conversation is owed no further answers.

    Raised when the *last* notification an agent run sent comes back answered,
    expired or cancelled -- not the first. An agent that messaged four people
    and woke on each reply would replay the whole conversation four times to
    learn "three still pending" three times over.

    An event rather than a call, because the work it triggers belongs to
    `agent`: bringing the asking conversation back. Both respond paths used to
    have to remember to do it themselves, through a function in the composition
    root that swallowed every failure into a log line -- so an answer whose
    delivery failed was simply lost. On the stream it is redelivered instead.
    """

    event_type: str = "notification.settled"
    pod_id: UUID
    conversation_id: UUID
    notification_id: UUID

    @classmethod
    def stream_name(cls) -> str:
        return SurfaceEvents.STREAM


class NotificationClosedEvent(DomainEvent):
    """A notification its asker holds something on has closed, however it closed.

    Raised for an origin outside this module that keeps something waiting
    until a person answers -- a schedule's held event, today -- when the
    notification is answered, expires or is cancelled. Each settles the held
    thing differently and only its owner knows how, so this says what happened
    and carries the ``action`` the asker wrote, with the option the person
    chose as ``answer``.

    An event rather than a call, for the reason ``NotificationSettledEvent``
    is one: this module may not reach into its asker, and an answer whose
    handling failed is redelivered rather than lost.
    """

    event_type: str = "notification.closed"
    pod_id: UUID
    notification_id: UUID
    origin_kind: NotificationOriginKind
    origin_id: UUID | None = None
    status: NotificationStatus
    #: Who answered. None when it expired or was cancelled.
    responder_user_id: UUID | None = None
    #: Whether the answer is the responder's own say-so: chosen by them in the
    #: app, or approved word for word as their agent drafted it. False for an
    #: answer their agent gave on its own, which is the agent's, not theirs.
    owner_confirmed: bool = False
    answer: str | None = None
    action: dict[str, JsonValue] | None = None

    @classmethod
    def of(
        cls, notification: NotificationEntity, *, owner_confirmed: bool = False
    ) -> NotificationClosedEvent:
        """How ``notification`` closed, as its asker reads it."""
        answer = (notification.response_data or {}).get("answer")
        return cls(
            pod_id=notification.pod_id,
            notification_id=notification.id,
            origin_kind=notification.origin_kind,
            origin_id=notification.origin_id,
            status=notification.status,
            responder_user_id=(
                notification.recipient_user_id
                if notification.status is NotificationStatus.RESPONDED
                else None
            ),
            owner_confirmed=owner_confirmed
            and notification.status is NotificationStatus.RESPONDED,
            answer=answer if isinstance(answer, str) else None,
            action=notification.action,
        )

    @classmethod
    def stream_name(cls) -> str:
        return SurfaceEvents.STREAM


def closed_events(
    notification: NotificationEntity, owner_confirmed: bool = False
) -> list[NotificationClosedEvent]:
    """What closing ``notification`` announces: its asker's event, if it has one.

    Every way out announces -- an answer, an expiry, a cancellation -- since a
    held thing nobody is told about stays held.
    """
    if not notification.announces_close:
        return []
    return [NotificationClosedEvent.of(notification, owner_confirmed=owner_confirmed)]


class SurfaceWebhookReceivedEvent(DomainEvent):
    event_type: str = "surface.webhook.received"
    source: str
    payload: dict[str, Any]
    headers: dict[str, str] | None = None
    surface_id: UUID | None = None
    source_event_id: str | None = None
    # Surfaces served by the native receiver (bot) that produced this event, so
    # platform-fan-in ingress can scope candidates to the receiving bot.
    receiver_surface_ids: list[UUID] | None = None

    @classmethod
    def stream_name(cls) -> str:
        return SurfaceEvents.STREAM
