"""Surface domain events published to the ``surface_events`` Redis stream."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.domain.events import DomainEvent


class SurfaceEvents:
    STREAM = "surface_events"


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
