from __future__ import annotations

from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.models import SurfaceMessageMetadata


SurfaceReplyKind = Literal[
    "signup",
    "identity_link",
    "surface_setup",
    "pod_access",
]


class SurfaceContextBase(BaseModel):
    platform: SurfacePlatform
    surface_id: UUID | None = None
    surface_account_id: UUID | None = None
    surface_config: SurfaceConfig | None = None
    agent_display_name: str | None = None
    event: ParsedInboundSurfaceEvent


class SurfaceReplyContext(SurfaceContextBase):
    """Send a direct reply on the platform without starting an agent run
    (signup prompts, contact-link requests/confirmations)."""

    mode: Literal["reply"] = "reply"
    reply_kind: SurfaceReplyKind = "signup"
    reply_message: str
    reply_metadata: dict = Field(default_factory=dict)


class SurfaceChatContext(SurfaceContextBase):
    mode: Literal["chat"] = "chat"
    pod_id: UUID | None = None
    agent_name: str | None = None
    conversation_id: UUID
    user_id: UUID
    message_text: str
    message_metadata: SurfaceMessageMetadata
    message_user_id: UUID
    message_external_user_id: str | None = None
    message_external_message_id: str | None = None


AgentSurfaceContext = Annotated[
    Union[SurfaceReplyContext, SurfaceChatContext],
    Field(discriminator="mode"),
]
