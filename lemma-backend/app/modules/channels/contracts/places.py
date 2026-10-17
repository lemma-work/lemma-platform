"""What a surface may ask of channels about places: where something is heard,
how to answer it, and a shared place's settings.

A place is a channel source. A surface finds the place a message came from,
notes what it heard there, and asks where a conversation's answer goes. A group
the pod's bot is in is a shared place, and carries the settings a group has:
who answers for its outsiders, whether they are answered, the link people join
a WhatsApp group by, and a creation the bot asked a platform for.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.entities import Source, SourceKind
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.source_repository import (
    GROUP_SETTINGS,
    SourceRepository,
)
from app.modules.channels.services.places import ChannelPlaces, Place

__all__ = [
    "GROUP_SETTINGS",
    "ReplyAddress",
    "addressed_at",
    "change_place",
    "confirm_group",
    "conversation_at",
    "find_place",
    "forget_chat",
    "forget_pending_group",
    "heard_at",
    "open_pending_group",
    "owned_place",
    "people_places",
    "person_places",
    "place_by_request",
    "places_hearing",
    "private_places_of",
    "reply_address",
    "shared_places_of_pod",
]


@dataclass(frozen=True, slots=True)
class ReplyAddress:
    """Where a conversation's answer goes, and what answers it in place.

    ``ref`` is the surface's own record of the line being answered -- or, for a
    conversation nobody wrote into from outside, of the last thing heard at
    the place. None when the place has heard nothing yet.
    """

    source: Source
    ref: dict[str, object] | None


async def find_place(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, place: Place
) -> Source | None:
    """The pod's source for a place, if it has been heard before. Makes nothing."""
    return await ChannelRepository(uow.session).find_source(
        pod_id=pod_id,
        surface_id=place.surface_id,
        platform=place.platform,
        external_channel_id=place.external_channel_id,
        external_user_id=place.external_user_id,
    )


async def heard_at(
    uow: SqlAlchemyUnitOfWork, source_id: UUID, delivery_ref: Mapping[str, object]
) -> None:
    """The surface heard this place just now; ``delivery_ref`` is how to answer."""
    await SourceRepository(uow.session).heard(source_id, delivery_ref)


async def addressed_at(
    uow: SqlAlchemyUnitOfWork, source_id: UUID, delivery_ref: Mapping[str, object]
) -> None:
    """How to write to a place nobody there has spoken in yet: a cold email."""
    await SourceRepository(uow.session).addressed(source_id, delivery_ref)


async def reply_address(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> ReplyAddress | None:
    """Where this conversation's answer goes, or None if it is said nowhere."""
    found = await SourceRepository(uow.session).reply_address(conversation_id)
    return ReplyAddress(source=found[0], ref=found[1]) if found else None


async def places_hearing(
    uow: SqlAlchemyUnitOfWork,
    *,
    platform: str | None,
    external_channel_id: str,
    surface_ids: Collection[UUID] | None = None,
) -> list[Source]:
    """The shared places on one chat: one per surface that hears it."""
    return await SourceRepository(uow.session).hearing(
        platform=platform,
        external_channel_id=external_channel_id,
        surface_ids=surface_ids,
    )


async def person_places(
    uow: SqlAlchemyUnitOfWork,
    *,
    platform: str,
    external_user_id: str,
    surface_ids: Collection[UUID] | None = None,
    pod_id: UUID | None = None,
    limit: int = 10,
) -> list[Source]:
    """One person's private places on a platform, freshest first -- in one
    pod, when ``pod_id`` names it."""
    return await SourceRepository(uow.session).person(
        platform=platform,
        external_user_id=external_user_id,
        surface_ids=surface_ids,
        pod_id=pod_id,
        limit=limit,
    )


async def people_places(
    uow: SqlAlchemyUnitOfWork,
    *,
    platform: str,
    external_user_ids: Collection[str],
    surface_id: UUID,
    pod_id: UUID,
) -> dict[str, Source]:
    """Each person's freshest private place on one surface, in one pod."""
    return await SourceRepository(uow.session).people(
        platform=platform,
        external_user_ids=external_user_ids,
        surface_id=surface_id,
        pod_id=pod_id,
    )


async def private_places_of(
    uow: SqlAlchemyUnitOfWork, channel_id: UUID
) -> list[Source]:
    """A channel's private places -- its person's chats -- freshest first."""
    return await SourceRepository(uow.session).of_channel(
        channel_id, kind=SourceKind.PRIVATE
    )


async def shared_places_of_pod(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID, *, limit: int = 200
) -> list[Source]:
    """The groups and shared channels the pod's bots are in."""
    return await SourceRepository(uow.session).shared_of_pod(pod_id, limit=limit)


async def conversation_at(
    uow: SqlAlchemyUnitOfWork,
    source_id: UUID,
    *,
    sender_external_id: str | None = None,
    external_thread_id: str | None = None,
) -> UUID | None:
    """The conversation that last took up something a person said at this
    place -- this sender, in this thread, when they are named. None when
    nothing said here was taken up."""
    return await SourceRepository(uow.session).conversation_at(
        source_id,
        sender_external_id=sender_external_id,
        external_thread_id=external_thread_id,
    )


async def place_by_request(
    uow: SqlAlchemyUnitOfWork, *, platform: str, request_id: str
) -> Source | None:
    return await SourceRepository(uow.session).by_request(
        platform=platform, request_id=request_id
    )


async def owned_place(
    uow: SqlAlchemyUnitOfWork, *, surface_id: UUID, owner_user_id: UUID, title: str
) -> Source | None:
    """A shared place this member answers for on this surface, by its title."""
    return await SourceRepository(uow.session).owned(
        surface_id=surface_id, owner_user_id=owner_user_id, title=title
    )


async def open_pending_group(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    surface_id: UUID,
    platform: str,
    title: str,
    owner_user_id: UUID,
    request_id: str,
    answers_outsiders: bool,
) -> Source:
    """A group the bot asked a platform to create, with its channel made now."""
    return await ChannelPlaces(uow).open_pending_group(
        pod_id=pod_id,
        surface_id=surface_id,
        platform=platform,
        title=title,
        owner_user_id=owner_user_id,
        request_id=request_id,
        answers_outsiders=answers_outsiders,
    )


async def confirm_group(
    uow: SqlAlchemyUnitOfWork,
    *,
    platform: str,
    request_id: str,
    external_channel_id: str,
    title: str | None,
    invite_link: str | None,
) -> Source | None:
    """The platform made the group the bot asked for. A replay changes nothing."""
    return await SourceRepository(uow.session).confirm(
        platform=platform,
        request_id=request_id,
        external_channel_id=external_channel_id,
        title=title,
        invite_link=invite_link,
    )


async def change_place(
    uow: SqlAlchemyUnitOfWork, source_id: UUID, values: Mapping[str, object]
) -> None:
    """Change a shared place's settings (``GROUP_SETTINGS``)."""
    await SourceRepository(uow.session).set(source_id, values)


async def forget_pending_group(
    uow: SqlAlchemyUnitOfWork, *, platform: str, request_id: str
) -> None:
    await SourceRepository(uow.session).forget_pending(
        platform=platform, request_id=request_id
    )


async def forget_chat(
    uow: SqlAlchemyUnitOfWork, *, platform: str, external_channel_id: str
) -> None:
    """A chat the platform deleted. Its channel and history stay."""
    await SourceRepository(uow.session).forget_chat(
        platform=platform, external_channel_id=external_channel_id
    )
