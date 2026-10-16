"""Payloads of the surfaces module's work items: ids, plus a request where there is no row.

A ``surface.ingest`` names its stored inbound row (or the held onboarding it
replays), so a retry reads the delivery as it was stored. A
``surface.platform_update`` carries its request inline: it is small (a
lifecycle event, a delivery status), nothing else needs to find it, and it is
gone once applied.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, model_validator

from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfacePlatformWebhookIngress,
)


class IngestPayload(BaseModel):
    """Route one stored inbound message, or replay one held onboarding."""

    inbound_message_id: UUID | None = None
    onboarding_pending_id: UUID | None = None

    @model_validator(mode="after")
    def _one_of(self) -> IngestPayload:
        if (self.inbound_message_id is None) == (self.onboarding_pending_id is None):
            raise ValueError(
                "name exactly one of inbound_message_id, onboarding_pending_id"
            )
        return self


class PlatformUpdatePayload(BaseModel):
    """A webhook that is about the app or a group, applied off the request."""

    request: SurfacePlatformWebhookIngress | SurfaceDirectWebhookIngress


class ReceiverPayload(BaseModel):
    """One native receiver: which credential, found again by its key at run time.

    No secret is stored: the handler re-reads the candidates and runs the one
    whose key matches, so a credential rotated or removed meanwhile is seen.
    """

    receiver_key: str
    platform: str


class MobileChangedPayload(BaseModel):
    user_id: UUID


class NotificationExpirePayload(BaseModel):
    notification_id: UUID


class NoPayload(BaseModel):
    pass


__all__ = [
    "IngestPayload",
    "MobileChangedPayload",
    "NoPayload",
    "NotificationExpirePayload",
    "PlatformUpdatePayload",
    "ReceiverPayload",
]
