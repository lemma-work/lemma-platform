"""The conversation a message continues, found by where it was said and who said it.

A message is said in a place -- a person's private chat, a group, an email
thread -- and every place is a source of a channel (``channel_places``). A
person has one conversation per agent in a channel, however many threads,
numbers, platforms or days they write across: the thread is new, the place
and the person are not. So binding is three steps -- place the message, note
what was heard there (how to answer it), and find or open the person's
conversation -- and only a different agent, or a different person, opens
another.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.delegation import agent_display_name
from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.domain.channel_names import configured_channel_name
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
)
from app.modules.agent_surfaces.domain.ports import SurfaceInstallationRepositoryPort
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    build_surface_event_metadata,
)
from app.modules.agent_surfaces.services.channel_places import (
    ChannelSeats,
    ChannelSeatsInDatabase,
    home_of,
    place_of,
)
from app.modules.agent_surfaces.services.contact_keepers import owned_or_moved
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)
from app.modules.channels.contracts import Source

_CONVERSATION_TITLE_MAX_LENGTH = 120


@dataclass(frozen=True, slots=True)
class BoundThread:
    """The conversation a message continues, and the place it was said."""

    conversation_id: UUID
    source_id: UUID
    #: Set only when this message opened the conversation: the moment worth
    #: naming the thread on the platform.
    created_title: str | None = None


class ConversationBinder:
    """Find or open the conversation a message belongs to."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        surface_repository: SurfaceInstallationRepositoryPort,
        seats: ChannelSeats | None = None,
    ) -> None:
        self.uow = uow
        self.surface_repository = surface_repository
        self.seats = seats or ChannelSeatsInDatabase(uow)

    async def bind_conversation(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        resolved_user: ResolvedSurfaceUser,
        route: ResolvedSurfaceRoute,
        contact_id: UUID | None = None,
    ) -> BoundThread:
        """The person's conversation with the answering agent, where they wrote.

        ``resolved_user`` is the member the conversation is kept by: the
        person themselves, or -- for a contact (``contact_id``) -- the member
        who looks after them. Its ``external_user_id`` is the sender's own id
        on the platform, which keys their private place.

        A resolved member is a precondition: every caller refuses an
        unresolved sender well before here.
        """
        user_id = resolved_user.internal_user_id
        if user_id is None:
            raise ValueError("A conversation cannot be bound to an unresolved sender")
        place = place_of(
            surface=surface,
            parsed=parsed,
            route=route,
            external_user_id=resolved_user.external_user_id,
        )
        source = await self.seats.source_for(
            pod_id=route.pod_id,
            place=place,
            home=home_of(place, user_id=user_id, contact_id=contact_id),
        )
        await self.seats.heard_at(source.id, parsed.model_dump(mode="json"))
        conversation_id, title = await self._persons_conversation(
            source=source,
            surface=surface,
            parsed=parsed,
            resolved_user=resolved_user,
            user_id=user_id,
            contact_id=contact_id,
            route=route,
        )
        await self._note_surface(
            conversation_id=conversation_id,
            surface=surface,
            parsed=parsed,
            resolved_user=resolved_user,
            route=route,
        )
        return BoundThread(
            conversation_id=conversation_id, source_id=source.id, created_title=title
        )

    async def _persons_conversation(
        self,
        *,
        source: Source,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        resolved_user: ResolvedSurfaceUser,
        user_id: UUID,
        contact_id: UUID | None,
        route: ResolvedSurfaceRoute,
    ) -> tuple[UUID, str | None]:
        """The person's conversation with this agent in the channel, opened if new.

        Opening is serialised on the person and the channel, so two first
        messages open one. The lock lasts only the transaction, and
        authorizing the person commits -- it hands the connection back -- so
        they are authorized first, and the lock taken after, around nothing
        but looking again and opening.
        """
        found = await self._kept(
            source=source, user_id=user_id, contact_id=contact_id, route=route
        )
        if found is not None:
            return found, None
        auth_ctx = await create_authorization_data_service(self.uow).build_user_context(
            user_id=user_id, pod_id=route.pod_id
        )
        token = set_current_context(auth_ctx)
        try:
            await agent_conversations.authorize_surface_conversation(
                self.uow,
                pod_id=route.pod_id,
                agent_name=route.agent_name,
                user_id=user_id,
            )
            await self.seats.lock_person(
                channel_id=source.channel_id,
                pod_id=route.pod_id,
                agent_id=route.agent_id,
                person_id=contact_id or user_id,
            )
            # Another message from them may have opened it while they were
            # being authorized.
            found = await self._kept(
                source=source, user_id=user_id, contact_id=contact_id, route=route
            )
            if found is not None:
                return found, None
            opened = await agent_conversations.open_surface_conversation(
                self.uow,
                pod_id=route.pod_id,
                agent_name=route.agent_name,
                user_id=user_id,
                contact_id=contact_id,
                channel_id=source.channel_id,
                title=_title(
                    parsed, fallback=f"{surface.surface_type.value} Conversation"
                ),
                metadata=_surface_metadata(surface, parsed, resolved_user, route),
                # Checked above, so nothing between the lock and the insert
                # releases the connection.
                require_execute_grant=False,
            )
            return opened.id, opened.title
        finally:
            reset_current_context(token)

    async def _kept(
        self,
        *,
        source: Source,
        user_id: UUID,
        contact_id: UUID | None,
        route: ResolvedSurfaceRoute,
    ) -> UUID | None:
        """The person's conversation with this agent in the channel, if any.

        A contact's found conversation must still be kept by ``user_id`` -- or
        move to them -- or one keeper's history would be handed to another.
        """
        found = await self.seats.conversation_in_channel(
            channel_id=source.channel_id,
            pod_id=route.pod_id,
            agent_id=route.agent_id,
            user_id=user_id,
            contact_id=contact_id,
        )
        if found is None:
            return None
        conversation = await agent_conversations.surface_conversation(self.uow, found)
        if conversation is not None and await owned_or_moved(
            self.uow, conversation, user_id, contact_id=contact_id
        ):
            return found
        return None

    async def _note_surface(
        self,
        *,
        conversation_id: UUID,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        resolved_user: ResolvedSurfaceUser,
        route: ResolvedSurfaceRoute,
    ) -> None:
        """Where the conversation was last written to, on its own metadata."""
        await self.surface_repository.merge_conversation_metadata(
            conversation_id, _surface_metadata(surface, parsed, resolved_user, route)
        )


def _surface_metadata(
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
    resolved_user: ResolvedSurfaceUser,
    route: ResolvedSurfaceRoute,
) -> dict[str, object]:
    surface_event_metadata = build_surface_event_metadata(
        surface.surface_type.value, parsed.metadata
    )
    return {
        "source": "agent_surfaces",
        "surface_id": str(surface.id),
        "surface_platform": surface.surface_type.value,
        "external_channel_id": parsed.external_channel_id,
        "channel_name": configured_channel_name(surface, parsed),
        "external_thread_id": parsed.external_thread_id,
        "external_user_id": resolved_user.external_user_id,
        "external_message_id": parsed.external_message_id,
        "route_key": route.route_key,
        "conversation_kind": route.conversation_kind,
        "routed_agent_id": str(route.agent_id) if route.agent_id else None,
        # From the route, which names the agent that is answering, not the
        # surface, which only says whose installation it is.
        "agent_display_name": agent_display_name(route.agent_name),
        "surface_event_metadata": (
            surface_event_metadata.model_dump(mode="json")
            if surface_event_metadata
            else None
        ),
    }


def _title(parsed: ParsedInboundSurfaceEvent, *, fallback: str) -> str:
    title = " ".join((parsed.message_text or "").split())
    if not title:
        return fallback
    if len(title) <= _CONVERSATION_TITLE_MAX_LENGTH:
        return title
    return f"{title[: _CONVERSATION_TITLE_MAX_LENGTH - 3].rstrip()}..."
