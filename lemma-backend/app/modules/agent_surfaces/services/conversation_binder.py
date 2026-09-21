"""The link between a surface thread and the conversation behind it.

One lifecycle: find the conversation this thread maps to, create it when there
isn't one, decide when a DM has gone cold enough to start a fresh one, and keep
the conversation's surface metadata in step.

An object with a constructor rather than a mixin, and the dependency direction
is why: inbound and interactions both reach for `get_or_create`, and this
reaches for neither. It was flattened onto one service only so they could see
it, which is also how the two *callers* came to be unable to see their own
collaborators' types -- an attribute that resolves to nothing type-checks as
nothing, and most of this module's baselined errors are that.
"""

from __future__ import annotations

from app.core.authorization.delegation import agent_display_name, effective_agent_id
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)

from datetime import datetime, timedelta, timezone
from uuid import UUID


from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    ThreadShape,
    thread_shape,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.channel_names import configured_channel_name
from app.modules.agent_surfaces.domain.ports import SurfaceInstallationRepositoryPort
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    build_surface_event_metadata,
)


_CONVERSATION_TITLE_MAX_LENGTH = 120
# Recent thread/channel messages fetched per run for group-mention continuity.


def should_start_a_new_conversation(
    *,
    surface: AgentSurfaceEntity,
    link: AgentSurfaceConversationLink,
    route: ResolvedSurfaceRoute | None = None,
    current_conversation_agent_id: UUID | None = None,
) -> bool:
    """Has this thread stopped being the conversation it was?

    Two reasons, and they are not the same reason.

    A **different agent** is a different conversation on every shape. This
    check used to sit inside a ``surface.mode is DM`` guard, so an email
    thread re-routed to another agent kept the old one indefinitely.

    A **cold thread** is only a fresh conversation where one thread id
    carries all of them. On a channel or an email thread the platform
    already bounded the topic, and cutting it on a timer discards history
    the person can still see above your reply.
    """

    # Compared through `effective_agent_id`, because the two sides are
    # written in different eras and the assistant has more than one spelling.
    # A conversation now names it by the pod's own id; a route computed from
    # surface configuration still names it by naming nobody. Raw, those two
    # differ, and this reads "the agent changed" for a thread whose agent
    # never changed -- cutting a fresh conversation and stranding the history
    # the person can still see above the reply.
    def same_agent(left: UUID | None, right: UUID | None, *, pod_id: UUID) -> bool:
        return effective_agent_id(left, pod_id=pod_id) == effective_agent_id(
            right, pod_id=pod_id
        )

    if (
        route is not None
        and current_conversation_agent_id is not None
        and not same_agent(
            current_conversation_agent_id, route.agent_id, pod_id=route.pod_id
        )
    ):
        return True
    if route is not None and not same_agent(
        link.routed_agent_id, route.agent_id, pod_id=route.pod_id
    ):
        return True
    shape = thread_shape(
        link.conversation_kind or (route.conversation_kind if route else None)
    )
    if shape is not ThreadShape.MULTIPLEXED:
        return False
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


class ConversationBinder:
    """Find or open the conversation a surface thread belongs to."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        surface_repository: SurfaceInstallationRepositoryPort,
        conversation_link_repository: SurfaceConversationLinkRepository,
    ) -> None:
        self.uow = uow
        self.surface_repository = surface_repository
        self.conversation_link_repository = conversation_link_repository

    async def bind_conversation(
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

        A resolved sender is a precondition, not a hope: a conversation belongs
        to somebody, and `open_surface_conversation` takes a user id. Both
        callers already refuse an unresolved sender well before here -- ingestion
        answers them with a signup link instead -- so this states the contract
        rather than adding a case. It is stated because this is a published verb
        now (`ConversationLinker`), and the caller that reaches it through that
        protocol has no way to see the check the others do.
        """
        if resolved_user.internal_user_id is None:
            raise ValueError("A conversation cannot be bound to an unresolved sender")
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
            if should_start_a_new_conversation(
                surface=surface,
                link=link,
                route=route,
                current_conversation_agent_id=current_conversation_agent_id,
            ):
                conversation = await self._create_surface_conversation(
                    user_id=resolved_user.internal_user_id,
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
                agent_name=route.agent_name,
            )
            return (updated or link), None

        conversation = await self._create_surface_conversation(
            surface=surface,
            parsed=parsed,
            resolved_user=resolved_user,
            user_id=resolved_user.internal_user_id,
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

    async def _create_surface_conversation(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        resolved_user: ResolvedSurfaceUser,
        user_id: UUID,
        external_user_id: str | None,
        route: ResolvedSurfaceRoute,
    ):
        surface_event_metadata = build_surface_event_metadata(
            surface.surface_type.value,
            parsed.metadata,
        )
        auth_ctx = await create_authorization_data_service(self.uow).build_user_context(
            user_id=user_id,
            pod_id=route.pod_id,
        )
        token = set_current_context(auth_ctx)
        try:
            return await agent_conversations.open_surface_conversation(
                self.uow,
                pod_id=route.pod_id,
                agent_name=route.agent_name,
                user_id=user_id,
                title=self._surface_conversation_title(
                    parsed,
                    fallback=f"{surface.surface_type.value} Conversation",
                ),
                metadata={
                    "source": "agent_surfaces",
                    "surface_id": str(surface.id),
                    "surface_platform": surface.surface_type.value,
                    "external_channel_id": parsed.external_channel_id,
                    "channel_name": configured_channel_name(surface, parsed),
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
        agent_name: str | None = None,
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
            "channel_name": configured_channel_name(surface, parsed),
            "external_thread_id": parsed.external_thread_id,
            "external_user_id": external_user_id,
            "external_message_id": parsed.external_message_id,
            "route_key": route_key,
            "conversation_kind": conversation_kind,
            "routed_agent_id": str(routed_agent_id) if routed_agent_id else None,
            # From the route, which names the agent that is answering. Reading
            # it off the surface answered "whose installation is this", which is
            # a different question wherever the two differ -- and the reason the
            # personal path had to hand this function a doctored surface.
            "agent_display_name": agent_display_name(agent_name),
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
