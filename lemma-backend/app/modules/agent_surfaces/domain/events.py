"""Surface domain events published to the ``surface_events`` Redis stream."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.domain.events import DomainEvent


class SurfaceEvents:
    STREAM = "surface_events"


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


class SurfaceMessageAnsweredEvent(DomainEvent):
    """An agent answered a member on a surface.

    Not raised at ingress: `execute_chat` only *starts* the run and cannot know
    whether an answer followed. This is projected from the agent run's own
    completion, where the outcome is known.
    """

    event_type: str = "surface.message.answered"
    surface_id: UUID
    pod_id: UUID
    agent_id: UUID | None = None

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
