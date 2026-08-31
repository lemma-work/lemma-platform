from __future__ import annotations

from typing import Annotated, Any, Literal, Union
from uuid import UUID

from pydantic import BaseModel, Field


class SurfacePlatformWebhookIngress(BaseModel):
    ingress_type: Literal["platform_webhook"] = "platform_webhook"
    source: str
    payload: dict[str, Any]
    headers: dict[str, str] = Field(default_factory=dict)
    # When the event was delivered by a native receiver (e.g. Telegram polling)
    # that knows exactly which bot it polled, these scope candidate surfaces to
    # the ones actually served by that bot — so a custom bot's update can't be
    # mis-attributed to another bot's surface. None = platform-wide fan-in.
    receiver_surface_ids: list[UUID] | None = None


class SurfaceDirectWebhookIngress(BaseModel):
    ingress_type: Literal["surface_webhook"] = "surface_webhook"
    surface_id: UUID
    payload: dict[str, Any]
    headers: dict[str, str] = Field(default_factory=dict)


SurfaceIngressRequest = Annotated[
    Union[
        SurfacePlatformWebhookIngress,
        SurfaceDirectWebhookIngress,
    ],
    Field(discriminator="ingress_type"),
]
