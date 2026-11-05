"""What a surface may ask of channels about places: where something is heard,
how to answer it, and a shared place's settings.

A place is a channel source, in one external account. A surface finds the
place a message came from, notes what it heard there, and asks where a
conversation's answer goes. Every lookup is scoped to the surfaces asking (or
to one pod), so one pod's bot never finds or forgets another pod's place.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    Principal,
    SeatedBy,
    Source,
)
from app.modules.channels.infrastructure.accounts import AccountRepository
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.rows import now
from app.modules.channels.infrastructure.surface_places import shared_places_page
from app.modules.channels.infrastructure.source_repository import (
    GROUP_SETTINGS,
    SourceRepository,
)
from app.modules.channels.services.places import (
    ChannelPlaces,
    GroupHome,
    Place,
    recompute_visibility,
)

__all__ = [
    "GROUP_SETTINGS",
    "ReplyAddress",
    "addressed_at",
    "adopt_place",
    "change_place",
    "confirm_group",
    "conversation_at",
    "find_place",
    "forget_chat",
    "forget_pending_group",
    "joined_place",
    "left_place",
    "open_pending_group",
    "owned_place",
    "people_places",
    "person_places",
    "place_became_private",
    "place_by_request",
    "place_heard_on",
    "place_resumed",
    "place_stopped",
    "places_hearing",
    "places_of",
    "reply_address",
    "set_place_owner",
    "shared_places_of_pod",
    "shared_places_of_surfaces",
    "stop_places_of_surfaces",
    "surface_heard_from",
    "surfaces_hearing",
]


@dataclass(frozen=True, slots=True)
class ReplyAddress:
    """Where a conversation's answer goes, and what answers it in place."""

    source: Source
    ref: dict[str, object] | None


async def find_place(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, place: Place
) -> Source | None:
    """The pod's source for a place, if it has been heard before. Makes nothing
    but the account."""
    account = await AccountRepository(uow.session).account_for(
        pod_id=pod_id,
        platform=place.platform,
        key=place.account,
        surface_id=place.surface_id,
    )
    return await SourceRepository(uow.session).find(
        account_id=account.id,
        external_channel_id=place.external_channel_id,
        external_user_id=place.external_user_id,
    )


async def addressed_at(
    uow: SqlAlchemyUnitOfWork, source_id: UUID, delivery_ref: Mapping[str, object]
) -> None:
    """How to write to this place from now: what answers the message just
    heard there, or -- where nobody there has spoken yet -- what writing there
    first learnt (a cold email). Not when it was heard: that is its line's to
    say (``channels.infrastructure.lines``), once."""
    await SourceRepository(uow.session).addressed(source_id, delivery_ref)


async def reply_address(
    uow: SqlAlchemyUnitOfWork,
    conversation_id: UUID,
    *,
    at_source_id: UUID | None = None,
) -> ReplyAddress | None:
    """Where this conversation's answer goes, or None if it is said nowhere.

    ``at_source_id`` keeps it to the place a run was asked at, whoever has
    written from elsewhere since: the latest line said there, else -- nobody
    there has spoken in this conversation yet -- the place's last word. None
    once the place is gone."""
    sources = SourceRepository(uow.session)
    found = await sources.reply_address(conversation_id, at_source_id=at_source_id)
    if found is None and at_source_id is not None:
        asked = await sources.get(at_source_id)
        found = (asked, asked.delivery_ref) if asked is not None else None
    return ReplyAddress(source=found[0], ref=found[1]) if found else None


async def places_hearing(
    uow: SqlAlchemyUnitOfWork,
    *,
    external_channel_id: str,
    surface_ids: Collection[UUID] | None,
    platform: str | None = None,
) -> list[Source]:
    """The shared places on one chat, among these surfaces' accounts. ``None``
    for every account on the platform: only where the request's signature
    proved no narrower scope."""
    return await SourceRepository(uow.session).hearing(
        external_channel_id=external_channel_id,
        surface_ids=surface_ids,
        platform=platform,
    )


async def place_heard_on(
    uow: SqlAlchemyUnitOfWork, *, surface_id: UUID, external_channel_id: str
) -> Source | None:
    """The shared place this surface hears on this chat, if it knows one."""
    found = await places_hearing(
        uow, external_channel_id=external_channel_id, surface_ids=[surface_id]
    )
    return found[0] if found else None


async def adopt_place(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, place: Place
) -> tuple[Source, bool]:
    """A shared place, made -- with its GROUP channel -- on first sight, and
    whether it was made now. A title the platform names it by now replaces the
    one it was known by."""
    known = await find_place(uow, pod_id=pod_id, place=place)
    if known is None:
        source = await ChannelPlaces(uow).source_for(
            pod_id=pod_id, place=place, home=GroupHome(title=place.title)
        )
        return source, True
    if place.title and known.title != place.title:
        await change_place(uow, known.id, {"title": place.title})
        known = known.model_copy(update={"title": place.title})
    return known, False


async def person_places(
    uow: SqlAlchemyUnitOfWork,
    *,
    platform: str,
    external_user_id: str,
    surface_ids: Collection[UUID] | None = None,
    pod_id: UUID | None = None,
    limit: int = 10,
) -> list[Source]:
    """One person's private places on a platform, freshest first, among these
    surfaces or in one pod (one of the two is required)."""
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


async def places_of(uow: SqlAlchemyUnitOfWork, channel_id: UUID) -> list[Source]:
    """A channel's places, the one heard from most recently first."""
    return await SourceRepository(uow.session).of_channel(channel_id)


async def shared_places_of_pod(
    uow: SqlAlchemyUnitOfWork,
    pod_id: UUID,
    *,
    limit: int = 200,
    surface_id: UUID | None = None,
) -> list[Source]:
    """The groups and shared channels the pod's bots are in; one bot's when named."""
    return await SourceRepository(uow.session).shared_of_pod(
        pod_id, limit=limit, surface_id=surface_id
    )


async def shared_places_of_surfaces(
    uow: SqlAlchemyUnitOfWork,
    surface_ids: Collection[UUID],
    *,
    after: UUID | None = None,
    limit: int = 200,
) -> list[Source]:
    """Every group and shared channel these surfaces' bots are in, a page at a
    time: pass the last one's id as ``after`` for the next. Through the
    surfaces' own accounts, so another app in the same workspace is not read."""
    return await shared_places_page(uow.session, surface_ids, after=after, limit=limit)


async def conversation_at(
    uow: SqlAlchemyUnitOfWork,
    source_id: UUID,
    *,
    sender_external_id: str | None = None,
    external_thread_id: str | None = None,
) -> UUID | None:
    """The conversation that last took up something a person said here."""
    return await SourceRepository(uow.session).conversation_at(
        source_id,
        sender_external_id=sender_external_id,
        external_thread_id=external_thread_id,
    )


async def place_by_request(
    uow: SqlAlchemyUnitOfWork,
    *,
    request_id: str,
    surface_ids: Collection[UUID] | None = None,
    platform: str | None = None,
) -> Source | None:
    """A pending group by the platform's request id, on these surfaces or --
    for a platform whose request ids are unique everywhere -- that platform."""
    return await SourceRepository(uow.session).by_request_on_surfaces(
        request_id=request_id, surface_ids=surface_ids, platform=platform
    )


async def surface_heard_from(uow: SqlAlchemyUnitOfWork, surface_id: UUID) -> bool:
    """Whether anybody has written to this surface's bot: what it receives
    arrives."""
    return await AccountRepository(uow.session).surface_heard_from(surface_id)


async def surfaces_hearing(
    uow: SqlAlchemyUnitOfWork, *, platform: str, external_channel_id: str
) -> list[UUID]:
    """The surfaces whose bots are in this chat -- to route what arrives there."""
    return await SourceRepository(uow.session).surfaces_hearing(
        platform=platform, external_channel_id=external_channel_id
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
    place: Place,
    title: str,
    owner_user_id: UUID,
    request_id: str,
    answers_outsiders: bool,
) -> Source:
    """A group the bot asked a platform to create, with its channel made now."""
    return await ChannelPlaces(uow).open_pending_group(
        pod_id=pod_id,
        place=place,
        title=title,
        owner_user_id=owner_user_id,
        request_id=request_id,
        answers_outsiders=answers_outsiders,
    )


async def confirm_group(
    uow: SqlAlchemyUnitOfWork,
    *,
    request_id: str,
    external_channel_id: str,
    title: str | None,
    invite_link: str | None,
    surface_ids: Collection[UUID] | None = None,
    platform: str | None = None,
) -> Source | None:
    """The platform made the group the bot asked for. A replay changes nothing."""
    return await SourceRepository(uow.session).confirm(
        request_id=request_id,
        surface_ids=surface_ids,
        platform=platform,
        external_channel_id=external_channel_id,
        title=title,
        invite_link=invite_link,
    )


async def change_place(
    uow: SqlAlchemyUnitOfWork, source_id: UUID, values: Mapping[str, object]
) -> None:
    """Change a shared place's settings (``GROUP_SETTINGS``)."""
    await SourceRepository(uow.session).set(source_id, values)


async def set_place_owner(
    uow: SqlAlchemyUnitOfWork, source_id: UUID, owner_user_id: UUID | None
) -> None:
    """The member who answers for a shared place -- who is in its channel
    too, so they can read what they answer for."""
    sources = SourceRepository(uow.session)
    await sources.set(source_id, {"owner_user_id": owner_user_id})
    if owner_user_id is None:
        return
    source = await sources.get(source_id)
    channel = (
        await ChannelRepository(uow.session).get(source.channel_id) if source else None
    )
    if channel is not None:
        await MemberRepository(uow.session).add_person(
            channel, Principal.member(owner_user_id), seated_by=SeatedBy.PLATFORM
        )


async def forget_pending_group(
    uow: SqlAlchemyUnitOfWork,
    *,
    request_id: str,
    surface_ids: Collection[UUID] | None = None,
    platform: str | None = None,
) -> None:
    await SourceRepository(uow.session).forget_pending(
        request_id=request_id, surface_ids=surface_ids, platform=platform
    )


async def forget_chat(
    uow: SqlAlchemyUnitOfWork,
    *,
    external_channel_id: str,
    surface_ids: Collection[UUID] | None = None,
    platform: str | None = None,
) -> None:
    """A chat the platform deleted, on these surfaces (or, for ids unique on a
    platform, that platform). Its channel and history stay."""
    await SourceRepository(uow.session).forget_chat(
        external_channel_id=external_channel_id,
        surface_ids=surface_ids,
        platform=platform,
    )


# -- what the platform says changed about a mirrored place ------------------------


async def _mirrored_group(uow: SqlAlchemyUnitOfWork, source_id: UUID) -> Channel | None:
    """The GROUP channel a shared place feeds."""
    source = await SourceRepository(uow.session).get(source_id)
    if source is None:
        return None
    channel = await ChannelRepository(uow.session).get(source.channel_id)
    return (
        channel if channel is not None and channel.kind is ChannelKind.GROUP else None
    )


async def place_became_private(uow: SqlAlchemyUnitOfWork, source_id: UUID) -> bool:
    """The platform now keeps this place to its members: so is its channel,
    to the people in it. True if anything changed. One way only."""
    sources = SourceRepository(uow.session)
    if not await sources.became_private(source_id):
        return False
    source = await sources.get(source_id)
    if source is not None:
        await recompute_visibility(uow.session, source.channel_id)
    return True


async def left_place(
    uow: SqlAlchemyUnitOfWork, source_id: UUID, principal: Principal
) -> bool:
    """Somebody left the place this source is: out of its channel, if the
    platform is what seated them there (``remove_platform_seat``)."""
    channel = await _mirrored_group(uow, source_id)
    if channel is None:
        return False
    return await MemberRepository(uow.session).remove_platform_seat(
        channel.id, principal
    )


async def joined_place(
    uow: SqlAlchemyUnitOfWork,
    source_id: UUID,
    principal: Principal,
    *,
    name: str | None = None,
) -> bool:
    """Somebody joined the place this source is: in its channel, seated by the
    platform -- so its leaving takes them out again -- under the ``name`` it
    shows them by until they speak under another. True for a seat made now."""
    channel = await _mirrored_group(uow, source_id)
    if channel is None:
        return False
    return await MemberRepository(uow.session).add_person(
        channel, principal, seated_by=SeatedBy.PLATFORM, shown_as=name
    )


async def place_stopped(uow: SqlAlchemyUnitOfWork, source_id: UUID) -> bool:
    """The bot was taken out of the place: its channel is archived -- read as
    before by its people, and refusing lines that could no longer go there."""
    channel = await _mirrored_group(uow, source_id)
    if channel is None or channel.is_archived:
        return False
    await ChannelRepository(uow.session).change(channel.id, {"archived_at": now()})
    return True


async def place_resumed(uow: SqlAlchemyUnitOfWork, source_id: UUID) -> bool:
    """The bot is back in a place it was taken out of: its channel with it."""
    channel = await _mirrored_group(uow, source_id)
    if channel is None or not channel.is_archived:
        return False
    await ChannelRepository(uow.session).change(channel.id, {"archived_at": None})
    return True


async def stop_places_of_surfaces(
    uow: SqlAlchemyUnitOfWork, surface_ids: Collection[UUID], *, page: int = 200
) -> int:
    """These surfaces' bots taken out of every shared place at once -- their
    app removed from the workspace, which sends no leave per place. Each is
    stopped as ``place_stopped`` stops one; returns how many were not already."""
    stopped = 0
    after: UUID | None = None
    while found := await shared_places_page(
        uow.session, surface_ids, after=after, limit=page
    ):
        for source in found:
            stopped += await place_stopped(uow, source.id)
        after = found[-1].id
    return stopped
