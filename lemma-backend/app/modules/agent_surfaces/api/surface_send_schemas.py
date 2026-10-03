"""Request and response of ``POST /pods/{pod_id}/surfaces/{name}/send``.

Their own module because ``schemas.py`` is at the size ceiling.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class SurfaceSendRequest(BaseModel):
    """Send a proactive message to a pod member on this surface."""

    user_id: UUID = Field(..., description="Target pod member (Lemma user id).")
    message: str = Field(..., min_length=1, description="Message text to deliver.")


class SurfaceSendResponse(BaseModel):
    sent: bool
    channel: str | None = Field(
        None,
        description=(
            "Where it went: `chat`, or `email` when the chat's reply window had "
            "closed and it was sent to the member's email address instead."
        ),
    )
    detail: str | None = Field(None, description="Why it went where it did.")


__all__ = ["SurfaceSendRequest", "SurfaceSendResponse"]
