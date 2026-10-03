"""A message the bot sent, as a failed-delivery status or a quote finds it."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SurfaceOutboundMessage:
    id: UUID
    surface_id: UUID
    conversation_id: UUID | None
    notification_id: UUID | None
    platform: str
    external_message_id: str
    recipient: str | None
    kind: str
    body: str | None
    status: str
    error: str | None


@dataclass(frozen=True, slots=True)
class FailedDeliveryStatus:
    """One platform report that a message it accepted never arrived."""

    external_message_id: str
    recipient: str | None
    #: The platform's error code, as a string: WhatsApp's are numbers, and
    #: nothing here does arithmetic on them.
    code: str | None
    title: str | None
    #: The number that sent it, on a platform that has several.
    sender_id: str | None = None


__all__ = ["FailedDeliveryStatus", "SurfaceOutboundMessage"]
