"""Surface domain events published to the ``surface_events`` Redis stream."""

from __future__ import annotations

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
