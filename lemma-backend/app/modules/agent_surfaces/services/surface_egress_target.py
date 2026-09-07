"""Where an outbound message goes, before anything is said on it.

Resolution only: the link, the surface, the adapter and the credentials for a
reply, and the agent name it goes out under. Separate from the sends because
every one of them starts here and none of them needs the others.
"""

from __future__ import annotations

from pydantic import ValidationError

from app.core.authorization.delegation import DEFAULT_RESPONDER_NAME
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.services.surface_route_types import (
    SurfaceEgressTarget,
)

from typing import Any
from uuid import UUID


from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

# Recent thread/channel messages fetched per run for group-mention continuity.


class SurfaceEgressTargetMixin:
    async def _resolve_egress_target(
        self, conversation_id: UUID
    ) -> "SurfaceEgressTarget | None":
        """Resolve the surface/adapter/event for an outbound message.

        Returns None (never raises) when the conversation has no active surface
        link or its stored ``last_event`` is missing/unparseable, so callers in
        the agent-run path treat egress as best-effort.
        """
        link = await self.conversation_link_repository.get_by_conversation_id(
            conversation_id
        )
        if link is None:
            logger.debug(
                "agent_surfaces.ingress_service.surface_egress_skipped_no_conversation.diagnostic",
                conversation_id=conversation_id,
            )
            return None

        surface = await self.surface_repository.get(link.surface_id)
        if surface is None or not surface.is_active:
            logger.debug(
                "agent_surfaces.ingress_service.surface_egress_skipped_surface_missing.diagnostic",
                conversation_id=conversation_id,
                surface_id=link.surface_id,
            )
            return None

        adapter = self.adapter_registry.get(surface.surface_type)
        if adapter is None:
            logger.debug(
                "agent_surfaces.ingress_service.surface_egress_skipped_no_adapter.diagnostic",
                surface_type=surface.surface_type,
                conversation_id=conversation_id,
            )
            return None

        if not link.last_event:
            logger.debug(
                "agent_surfaces.ingress_service.surface_egress_skipped_missing_last.diagnostic",
                conversation_id=conversation_id,
            )
            return None
        try:
            parsed_event = ParsedInboundSurfaceEvent.model_validate(link.last_event)
        except ValidationError:
            logger.debug(
                "agent_surfaces.ingress_service.surface_egress_skipped_invalid_last.diagnostic",
                conversation_id=conversation_id,
            )
            return None

        credentials = await self._resolve_credentials(surface)
        return SurfaceEgressTarget(
            link=link,
            surface=surface,
            adapter=adapter,
            event=parsed_event,
            credentials=credentials,
        )

    async def _egress_metadata_with_agent_name(
        self,
        target: "SurfaceEgressTarget",
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        resolved = dict(metadata or {})
        # The link first: it records which agent actually answered this thread,
        # which is not always the one the surface points at now -- a surface can
        # be moved to another agent, and older threads keep the name they wore.
        agent_id = target.link.routed_agent_id or target.surface.agent_id
        agent = (
            await agent_conversations.surface_agent_identity(self.uow, agent_id)
            if agent_id
            else None
        )
        # Not `agent.name`: the pod's own agent is stored as `pod_default`,
        # an internal identifier that used to be absent entirely, so every
        # caller wrote `or "Lemma"` and the null did the work. It has a name of
        # its own now, and it is not the product's — see `DEFAULT_RESPONDER_NAME`.
        is_default = agent is None or agent.is_pod_default
        resolved.setdefault(
            "agent_display_name",
            DEFAULT_RESPONDER_NAME if is_default else agent.name,
        )
        if agent is not None and agent.icon_url:
            resolved.setdefault("agent_icon_url", str(agent.icon_url))
        return resolved
