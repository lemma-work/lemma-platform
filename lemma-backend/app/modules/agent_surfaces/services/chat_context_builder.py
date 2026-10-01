"""The run to start for a message that has found its conversation."""

from __future__ import annotations

from uuid import UUID

from app.modules.agent_surfaces.domain.channel_names import configured_channel_name
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
)
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.models import SurfaceMessageMetadata
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)


def build_chat_context(
    *,
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
    resolved_user: ResolvedSurfaceUser,
    user_id: UUID,
    route: ResolvedSurfaceRoute,
    conversation_id: UUID,
    created_conversation_title: str | None,
    answers_outsider: bool = False,
) -> SurfaceChatContext:
    """The run to start for a bound conversation, as the worker will read it.

    ``answers_outsider``: ``user_id`` is the member who answers for a group and
    ``resolved_user`` the stranger who asked -- see ``services/outsiders``.
    """
    return SurfaceChatContext(
        answers_outsider=answers_outsider,
        created_conversation_title=created_conversation_title,
        platform=surface.surface_type,
        pod_id=surface.pod_id,
        agent_name=route.agent_name,
        conversation_id=conversation_id,
        user_id=user_id,
        surface_id=surface.id,
        surface_name=surface.name,
        surface_account_id=surface.account_id,
        surface_config=surface.config,
        agent_display_name=route.agent_display_name,
        message_text=parsed.message_text,
        message_metadata=SurfaceMessageMetadata(
            surface_platform=surface.surface_type,
            sender_display_name=resolved_user.display_name,
            sender_email=resolved_user.email,
            sender_phone=resolved_user.phone,
            conversation_kind=route.conversation_kind,
            external_channel_id=parsed.external_channel_id,
            channel_name=configured_channel_name(surface, parsed),
            event_metadata=parsed.metadata,
        ),
        message_user_id=user_id,
        message_external_user_id=resolved_user.external_user_id,
        message_external_message_id=parsed.external_message_id,
        event=parsed,
    )
