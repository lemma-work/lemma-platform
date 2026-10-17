"""Which channel source a message arrived on, and whose channel it belongs to.

A surface hears a message somewhere; this names the place the way channels
key it, and says which channel a place heard for the first time belongs to:

* **A private chat** is keyed by the person on it, not by the address it came
  to -- a WhatsApp chat that arrives on another pool number, or a Slack direct
  message started afresh, is still the same place. It belongs to the member's
  DIRECT channel, or the contact's CONTACT channel.
* **A group or a shared channel** is keyed by the chat, and is a GROUP channel
  of its own.
* **An email thread** is keyed by the thread. With other people on it, it is a
  group; between the pod and one person, it is that person's.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Protocol
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts import channel_conversations

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.channel_names import configured_channel_name
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)
from app.modules.channels.contracts import (
    ContactsHome,
    GroupHome,
    Home,
    MembersHome,
    Place,
    Source,
    SourceKind,
    direct_channel,
    source_for,
)
from app.modules.channels.contracts import places
from app.modules.channels.contracts.places import ReplyAddress


class ChannelSeats(Protocol):
    """Where things sit in channels: places, what was heard at them, and the
    person's conversation there.

    What a surface asks of other modules about places, behind one seam so the
    binder, routing and delivery can be exercised without a database.
    """

    async def source_for(self, *, pod_id: UUID, place: Place, home: Home) -> Source: ...

    async def heard_at(
        self, source_id: UUID, delivery_ref: Mapping[str, object]
    ) -> None: ...

    async def addressed_at(
        self, source_id: UUID, delivery_ref: Mapping[str, object]
    ) -> None: ...

    async def lock_person(
        self, *, channel_id: UUID, pod_id: UUID, agent_id: UUID | None, person_id: UUID
    ) -> None: ...

    async def conversation_in_channel(
        self,
        *,
        channel_id: UUID,
        pod_id: UUID,
        agent_id: UUID | None,
        user_id: UUID,
        contact_id: UUID | None,
    ) -> UUID | None: ...

    async def direct_channel(self, *, pod_id: UUID, user_id: UUID) -> UUID: ...

    async def reply_address(self, conversation_id: UUID) -> ReplyAddress | None: ...

    async def person_places(
        self,
        *,
        platform: str,
        external_user_id: str,
        surface_ids: Collection[UUID] | None = None,
        pod_id: UUID | None = None,
        limit: int = 10,
    ) -> list[Source]: ...

    async def people_places(
        self,
        *,
        platform: str,
        external_user_ids: Collection[str],
        surface_id: UUID,
        pod_id: UUID,
    ) -> dict[str, Source]: ...

    async def places_hearing(
        self,
        *,
        platform: str | None,
        external_channel_id: str,
        surface_ids: Collection[UUID] | None = None,
    ) -> list[Source]: ...

    async def conversation_at(
        self,
        source_id: UUID,
        *,
        sender_external_id: str | None = None,
        external_thread_id: str | None = None,
    ) -> UUID | None: ...


class ChannelSeatsInDatabase:
    """The seats, as ``channels`` and ``agent`` keep them."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow

    async def source_for(self, *, pod_id: UUID, place: Place, home: Home) -> Source:
        return await source_for(self.uow, pod_id=pod_id, place=place, home=home)

    async def heard_at(
        self, source_id: UUID, delivery_ref: Mapping[str, object]
    ) -> None:
        await places.heard_at(self.uow, source_id, delivery_ref)

    async def addressed_at(
        self, source_id: UUID, delivery_ref: Mapping[str, object]
    ) -> None:
        await places.addressed_at(self.uow, source_id, delivery_ref)

    async def lock_person(
        self, *, channel_id: UUID, pod_id: UUID, agent_id: UUID | None, person_id: UUID
    ) -> None:
        await channel_conversations.lock_person_in_channel(
            self.uow,
            channel_id=channel_id,
            pod_id=pod_id,
            agent_id=agent_id,
            person_id=person_id,
        )

    async def conversation_in_channel(
        self,
        *,
        channel_id: UUID,
        pod_id: UUID,
        agent_id: UUID | None,
        user_id: UUID,
        contact_id: UUID | None,
    ) -> UUID | None:
        return await channel_conversations.conversation_in_channel(
            self.uow,
            channel_id=channel_id,
            pod_id=pod_id,
            agent_id=agent_id,
            user_id=user_id,
            contact_id=contact_id,
        )

    async def direct_channel(self, *, pod_id: UUID, user_id: UUID) -> UUID:
        return (await direct_channel(self.uow, pod_id=pod_id, user_id=user_id)).id

    async def reply_address(self, conversation_id: UUID) -> ReplyAddress | None:
        return await places.reply_address(self.uow, conversation_id)

    async def person_places(
        self,
        *,
        platform: str,
        external_user_id: str,
        surface_ids: Collection[UUID] | None = None,
        pod_id: UUID | None = None,
        limit: int = 10,
    ) -> list[Source]:
        return await places.person_places(
            self.uow,
            platform=platform,
            external_user_id=external_user_id,
            surface_ids=surface_ids,
            pod_id=pod_id,
            limit=limit,
        )

    async def people_places(
        self,
        *,
        platform: str,
        external_user_ids: Collection[str],
        surface_id: UUID,
        pod_id: UUID,
    ) -> dict[str, Source]:
        return await places.people_places(
            self.uow,
            platform=platform,
            external_user_ids=external_user_ids,
            surface_id=surface_id,
            pod_id=pod_id,
        )

    async def places_hearing(
        self,
        *,
        platform: str | None,
        external_channel_id: str,
        surface_ids: Collection[UUID] | None = None,
    ) -> list[Source]:
        return await places.places_hearing(
            self.uow,
            platform=platform,
            external_channel_id=external_channel_id,
            surface_ids=surface_ids,
        )

    async def conversation_at(
        self,
        source_id: UUID,
        *,
        sender_external_id: str | None = None,
        external_thread_id: str | None = None,
    ) -> UUID | None:
        return await places.conversation_at(
            self.uow,
            source_id,
            sender_external_id=sender_external_id,
            external_thread_id=external_thread_id,
        )


_GROUP = "CHANNEL"
_EMAIL = "EMAIL"


def is_shared(route: ResolvedSurfaceRoute, parsed: ParsedInboundSurfaceEvent) -> bool:
    """Whether several people read what is said in this place."""
    if route.conversation_kind == _GROUP:
        return True
    return route.conversation_kind == _EMAIL and bool(_copied(parsed))


def place_of(
    *,
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
    route: ResolvedSurfaceRoute,
    external_user_id: str | None,
) -> Place:
    """The place this message was said in, as a channel source names it."""
    platform = surface.surface_type.value
    if is_shared(route, parsed):
        where = (
            parsed.external_thread_id
            if route.conversation_kind == _EMAIL
            else parsed.external_channel_id
        )
        return Place(
            surface_id=surface.id,
            platform=platform,
            kind=SourceKind.SHARED,
            external_channel_id=where,
            external_user_id=None,
            title=_title(surface, parsed),
        )
    return Place(
        surface_id=surface.id,
        platform=platform,
        kind=SourceKind.PRIVATE,
        external_channel_id=(
            parsed.external_thread_id if route.conversation_kind == _EMAIL else None
        ),
        external_user_id=external_user_id,
    )


def home_of(place: Place, *, user_id: UUID, contact_id: UUID | None) -> Home:
    """The channel a place heard for the first time belongs to."""
    if place.kind is SourceKind.SHARED:
        return GroupHome(title=place.title)
    if contact_id is not None:
        return ContactsHome(contact_id=contact_id)
    return MembersHome(user_id=user_id)


def _copied(parsed: ParsedInboundSurfaceEvent) -> list[str]:
    copied = parsed.reply_target.get("cc") if parsed.reply_target else None
    return [address for address in copied or [] if isinstance(address, str)]


def _title(
    surface: AgentSurfaceEntity, parsed: ParsedInboundSurfaceEvent
) -> str | None:
    named = parsed.metadata.get("chat_title") or parsed.metadata.get("subject")
    if isinstance(named, str) and named.strip():
        return named.strip()[:255]
    return configured_channel_name(surface, parsed)
