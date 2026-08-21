"""Where an outbound message goes, before anything is said on it.

Resolution only: the link, the surface, the adapter and the credentials for a
reply, and the agent name it goes out under. Separate from the sends because
every one of them starts here and none of them needs the others.
"""

from __future__ import annotations

from app.modules.agent_surfaces.services.surface_route_types import (
    SurfaceEgressTarget,
)

from typing import Any
from uuid import UUID


from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

_CONVERSATION_TITLE_MAX_LENGTH = 120
# Recent thread/channel messages fetched per run for group-mention continuity.
_CHANNEL_CONTEXT_LIMIT = 15


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
        except Exception:
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
        agent_id = target.link.routed_agent_id or target.surface.agent_id
        # A pod-assistant route has no agent *by design*, so the usual
        # "fall back to the surface default" would put the default agent's name
        # and face on the pod assistant's replies.
        if self._routes_to_pod_assistant(target):
            agent_id = None
        agent = (
            await self.conversation_service.agent_repository.get(agent_id)
            if agent_id
            else None
        )
        resolved.setdefault(
            "agent_display_name", getattr(agent, "name", None) or "Lemma"
        )
        icon_url = getattr(agent, "icon_url", None)
        if icon_url:
            resolved.setdefault("agent_icon_url", str(icon_url))
        return resolved

    def _routes_to_pod_assistant(self, target: "SurfaceEgressTarget") -> bool:
        """True when this conversation is answered by the pod assistant.

        Two ways to get there, and both have to be checked: a *channel* routed
        to it, or a *person* who chose it for their own DMs. Checking only the
        channel left every pod-assistant DM wearing the default agent's name.
        """
        if target.surface.surface_type is SurfacePlatform.SLACK:
            external_user_id = str(getattr(target.link, "external_user_id", "") or "")
            if target.surface.config.slack.chose_pod_assistant(external_user_id):
                return True
        channel_id = str(getattr(target.link, "external_channel_id", "") or "")
        if not channel_id:
            return False
        route = target.surface.channel_route_for(channel_id=channel_id, channel_name="")
        return bool(route is not None and route.use_pod_assistant)
