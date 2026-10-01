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
    surface_name: str | None = None
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
    personal_dm_route_id: UUID | None = None
    pod_id: UUID | None = None
    agent_name: str | None = None
    conversation_id: UUID
    user_id: UUID
    message_text: str
    message_metadata: SurfaceMessageMetadata
    message_user_id: UUID
    message_external_user_id: str | None = None
    message_external_message_id: str | None = None
    # Set only on the replay of a message saved while the sender was being
    # onboarded: the pending row this turn hands off. Its commit marker is what
    # makes a queue retry unable to record or run the request twice.
    onboarding_handoff_id: UUID | None = None
    # Set only when this turn *created* the conversation, carrying its title.
    # It is how the platform learns a fresh thread began — the one moment worth
    # naming the thread on Slack. None on every subsequent message.
    created_conversation_title: str | None = None
    # Somebody outside the pod asked this, in a group that answers them. Then
    # `user_id` is not who wrote the message: it is the member who answers for
    # the group and owns the conversation, and nothing the message says may be
    # taken as that member deciding anything -- see `write_inbound_message`.
    answers_outsider: bool = False


AgentSurfaceContext = Annotated[
    Union[SurfaceReplyContext, SurfaceChatContext],
    Field(discriminator="mode"),
]
