"""The link between a surface thread and the conversation behind it.

One lifecycle: find the conversation this thread maps to, create it when there
isn't one, decide when a DM has gone cold enough to start a fresh one, and keep
the conversation's surface metadata in step.
"""

from __future__ import annotations

from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)

from datetime import datetime, timedelta, timezone
from uuid import UUID


from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceMode,
)
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    build_surface_event_metadata,
)


_CONVERSATION_TITLE_MAX_LENGTH = 120
# Recent thread/channel messages fetched per run for group-mention continuity.


class SurfaceConversationLinkMixin:
    async def _get_or_create_conversation_link(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        resolved_user: ResolvedSurfaceUser,
        route: ResolvedSurfaceRoute,
        current_conversation_agent_id: UUID | None = None,
    ) -> tuple[AgentSurfaceConversationLink, str | None]:
        """Return the link, plus the new conversation's title when one was created.

        The title is how a caller learns a *fresh* conversation started on this
        turn — which is the only moment worth naming the thread on the platform.
        None means the link already existed.
        """
        external_user_id = resolved_user.external_user_id
        link = await self.conversation_link_repository.get_by_external_thread(
            surface_id=surface.id,
            platform=surface.surface_type.value,
            external_channel_id=parsed.external_channel_id,
            external_thread_id=parsed.external_thread_id,
            external_user_id=external_user_id,
        )
        event_payload = parsed.model_dump(mode="json")
        if link is not None:
            if self._should_reset_dm_conversation(
                surface=surface,
                link=link,
                route=route,
                current_conversation_agent_id=current_conversation_agent_id,
            ):
                conversation = await self._create_surface_conversation(
                    surface=surface,
                    parsed=parsed,
                    resolved_user=resolved_user,
                    external_user_id=external_user_id,
                    route=route,
                )
                updated = await self.conversation_link_repository.update_conversation(
                    link_id=link.id,
                    conversation_id=conversation.id,
                    last_event=event_payload,
                    last_message_id=parsed.external_message_id,
                    routed_agent_id=route.agent_id,
                    conversation_kind=route.conversation_kind,
                    route_key=route.route_key,
                )
                return (updated or link), conversation.title
            updated = await self.conversation_link_repository.update_last_event(
                link_id=link.id,
                last_event=event_payload,
                last_message_id=parsed.external_message_id,
            )
            await self._update_conversation_surface_metadata(
                conversation_id=link.conversation_id,
                surface=surface,
                parsed=parsed,
                external_user_id=external_user_id,
                route_key=link.route_key or route.route_key,
                routed_agent_id=link.routed_agent_id or route.agent_id,
                conversation_kind=link.conversation_kind or route.conversation_kind,
            )
            return (updated or link), None

        conversation = await self._create_surface_conversation(
            surface=surface,
            parsed=parsed,
            resolved_user=resolved_user,
            external_user_id=external_user_id,
            route=route,
        )
        created_link = await self.conversation_link_repository.create(
            AgentSurfaceConversationLink(
                surface_id=surface.id,
                conversation_id=conversation.id,
                platform=surface.surface_type.value,
                external_channel_id=parsed.external_channel_id,
                external_thread_id=parsed.external_thread_id,
                external_user_id=external_user_id,
                routed_agent_id=route.agent_id,
                conversation_kind=route.conversation_kind,
                route_key=route.route_key,
                last_event=event_payload,
                last_message_id=parsed.external_message_id,
                # This row exists because they just wrote to us.
                last_inbound_at=datetime.now(timezone.utc),
            )
        )
        return created_link, conversation.title

    def _should_reset_dm_conversation(
        self,
        *,
        surface: AgentSurfaceEntity,
        link: AgentSurfaceConversationLink,
        route: ResolvedSurfaceRoute | None = None,
        current_conversation_agent_id: UUID | None = None,
    ) -> bool:
        if surface.mode is not SurfaceMode.DM:
            return False
        if (
            route is not None
            and current_conversation_agent_id is not None
            and current_conversation_agent_id != route.agent_id
        ):
            return True
        if route is not None and link.routed_agent_id != route.agent_id:
            return True
        reset_hours = surface.config.dm_conversation_reset_after_hours
        if reset_hours <= 0:
            return False
        # Inbound activity, NOT ``updated_at``: an outbound notification also
        # writes this row, so keying the reset off ``updated_at`` would let a
        # proactive message suppress it and leak yesterday's context into today.
        last_seen = link.inbound_activity_at
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - last_seen > timedelta(hours=reset_hours)

    async def _create_surface_conversation(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        resolved_user: ResolvedSurfaceUser,
        external_user_id: str | None,
        route: ResolvedSurfaceRoute,
    ):
        surface_event_metadata = build_surface_event_metadata(
            surface.surface_type.value,
            parsed.metadata,
        )
        auth_ctx = await create_authorization_data_service(self.uow).build_user_context(
            user_id=resolved_user.internal_user_id,
            pod_id=surface.pod_id,
        )
        token = set_current_context(auth_ctx)
        try:
            return await self.conversation_service.create_conversation(
                pod_id=surface.pod_id,
                agent_name=route.agent_name,
                user_id=resolved_user.internal_user_id,
                title=self._surface_conversation_title(
                    parsed,
                    fallback=f"{surface.surface_type.value} Conversation",
                ),
                metadata={
                    "source": "agent_surfaces",
                    "surface_id": str(surface.id),
                    "surface_platform": surface.surface_type.value,
                    "external_channel_id": parsed.external_channel_id,
                    "external_thread_id": parsed.external_thread_id,
                    "external_user_id": external_user_id,
                    "external_message_id": parsed.external_message_id,
                    "route_key": route.route_key,
                    "conversation_kind": route.conversation_kind,
                    "routed_agent_id": str(route.agent_id) if route.agent_id else None,
                    "agent_display_name": route.agent_display_name,
                    "surface_event_metadata": (
                        surface_event_metadata.model_dump(mode="json")
                        if surface_event_metadata
                        else None
                    ),
                },
            )
        finally:
            reset_current_context(token)

    async def _update_conversation_surface_metadata(
        self,
        *,
        conversation_id: UUID,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        external_user_id: str | None,
        route_key: str | None = None,
        routed_agent_id: UUID | None = None,
        conversation_kind: str | None = None,
    ) -> None:
        surface_event_metadata = build_surface_event_metadata(
            surface.surface_type.value,
            parsed.metadata,
        )
        updates = {
            "source": "agent_surfaces",
            "surface_id": str(surface.id),
            "surface_platform": surface.surface_type.value,
            "external_channel_id": parsed.external_channel_id,
            "external_thread_id": parsed.external_thread_id,
            "external_user_id": external_user_id,
            "external_message_id": parsed.external_message_id,
            "route_key": route_key,
            "conversation_kind": conversation_kind,
            "routed_agent_id": str(routed_agent_id) if routed_agent_id else None,
            "agent_display_name": await self.agent_name_for_surface(surface) or "Lemma",
            "surface_event_metadata": (
                surface_event_metadata.model_dump(mode="json")
                if surface_event_metadata
                else None
            ),
        }
        await self.surface_repository.merge_conversation_metadata(
            conversation_id, updates
        )

    def _surface_conversation_title(
        self,
        parsed: ParsedInboundSurfaceEvent,
        *,
        fallback: str,
    ) -> str:
        title = " ".join((parsed.message_text or "").split())
        if not title:
            return fallback
        if len(title) <= _CONVERSATION_TITLE_MAX_LENGTH:
            return title
        return f"{title[: _CONVERSATION_TITLE_MAX_LENGTH - 3].rstrip()}..."
