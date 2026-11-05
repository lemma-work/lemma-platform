"""Responses that need reads beyond the channel itself: names, unread counts,
places, last lines, members. A page of any size is a fixed number of reads.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.api.access import Caller, may_manage
from app.modules.channels.api.message_schemas import (
    PREVIEW_CHARS,
    ChannelMessageResponse,
    ChannelRef,
    hidden_from,
    message_agents,
    message_people,
    message_response,
)
from app.modules.channels.api.schemas import (
    AgentNames,
    ChannelDetailResponse,
    ChannelMemberResponse,
    ChannelResponse,
    LinePreview,
    channel_people,
    channel_response,
    member_response,
    my_membership,
    ref,
    source_response,
)
from app.modules.channels.api.schemas import (
    Names as NameMap,
)
from app.modules.channels.domain.entities import (
    Channel,
    ChannelMessage,
    Delivery,
    Member,
    Principal,
)
from app.modules.channels.infrastructure.deliveries import DeliveryRepository
from app.modules.channels.infrastructure.inbox import InboxReads
from app.modules.channels.infrastructure.lines import LineRepository
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.names import Names
from app.modules.channels.infrastructure.source_repository import SourceRepository
from app.modules.contacts.contracts.outreach import opted_out


async def _named(
    uow: SqlAlchemyUnitOfWork,
    pod_id: UUID,
    *,
    people: Sequence[Principal],
    agents: Sequence[UUID] = (),
) -> tuple[NameMap, AgentNames]:
    names = Names(uow)
    return (
        await names.of(pod_id=pod_id, people=people),
        await names.agents(pod_id=pod_id, agent_ids=agents),
    )


def _preview(
    line: ChannelMessage,
    channel: Channel,
    *,
    reader_id: UUID,
    names: NameMap,
    agents: AgentNames,
) -> LinePreview:
    shown = message_response(
        line, channel, reader_id=reader_id, names=names, agents=agents
    )
    hidden = hidden_from(line, channel, reader_id)
    return LinePreview(
        id=line.id,
        seq=line.seq,
        sender=shown.sender,
        text=(shown.text or "")[:PREVIEW_CHARS],
        withheld=hidden,
        deleted=line.is_deleted,
        created_at=line.created_at,
    )


async def channel_rows(
    uow: SqlAlchemyUnitOfWork,
    caller: Caller,
    channels: Sequence[Channel],
    *,
    unseen: Collection[UUID] = (),
) -> list[ChannelResponse]:
    """Each channel with what is new in it for the caller, its places and its
    last line -- in a few reads for the whole page. ``unseen``: channels the
    caller is shown without being able to read them (a pod admin's view of a
    bot's private groups), listed without their last line."""
    if not channels:
        return []
    ids = [channel.id for channel in channels]
    unread = await InboxReads(uow.session).unread(caller.viewer, channels)
    sources = await SourceRepository(uow.session).of_channels(ids)
    latest = await LineRepository(uow.session).by_ids(
        [
            channel.last_line_id
            for channel in channels
            if channel.last_line_id and channel.id not in unseen
        ]
    )
    names, agents = await _named(
        uow,
        caller.pod_id,
        people=[
            *(person for channel in channels for person in channel_people(channel)),
            *(person for line in latest.values() for person in message_people(line)),
        ],
        agents=[agent for line in latest.values() for agent in message_agents(line)],
    )
    rows: list[ChannelResponse] = []
    for channel in channels:
        line = latest.get(channel.last_line_id) if channel.last_line_id else None
        rows.append(
            channel_response(
                channel,
                names=names,
                unread=unread.get(channel.id),
                sources=sources.get(channel.id, []),
                last_line=(
                    _preview(
                        line,
                        channel,
                        reader_id=caller.user_id,
                        names=names,
                        agents=agents,
                    )
                    if line is not None
                    else None
                ),
            )
        )
    return rows


async def channel_detail(
    uow: SqlAlchemyUnitOfWork, caller: Caller, channel: Channel
) -> ChannelDetailResponse:
    """One channel, with its places, the people and agents in it, and how the
    caller hears of it."""
    (row,) = await channel_rows(uow, caller, [channel])
    members = await MemberRepository(uow.session).of_channel(channel.id)
    names, agents = await _named(
        uow,
        caller.pod_id,
        people=[member.principal for member in members if member.principal],
        agents=[member.agent_id for member in members if member.agent_id],
    )
    seat = next(
        (member for member in members if member.user_id == caller.user_id), None
    )
    return ChannelDetailResponse(
        **row.model_dump(),
        opted_out=(
            await opted_out(uow, pod_id=channel.pod_id, contact_id=channel.contact_id)
            if channel.contact_id is not None
            else None
        ),
        source_places=[
            source_response(source)
            for source in await SourceRepository(uow.session).of_channel(channel.id)
        ],
        members=[
            shown
            for member in members
            if (shown := member_response(member, names=names, agents=agents))
        ],
        me=my_membership(
            seat,
            read_seq=row.unread.read_seq,
            can_manage=await may_manage(uow, caller, channel),
        ),
    )


async def members_response(
    uow: SqlAlchemyUnitOfWork, caller: Caller, members: Sequence[Member]
) -> list[ChannelMemberResponse]:
    """People and agents in a channel, each by name."""
    names, agents = await _named(
        uow,
        caller.pod_id,
        people=[member.principal for member in members if member.principal],
        agents=[member.agent_id for member in members if member.agent_id],
    )
    return [
        shown
        for member in members
        if (shown := member_response(member, names=names, agents=agents))
    ]


async def lines_response(
    uow: SqlAlchemyUnitOfWork,
    caller: Caller,
    channel: Channel,
    lines: Sequence[ChannelMessage],
) -> list[ChannelMessageResponse]:
    """Lines of one channel, as the caller reads them -- each with where it
    went outside Lemma."""
    names, agents = await _named(
        uow,
        caller.pod_id,
        people=[person for line in lines for person in message_people(line)],
        agents=[agent for line in lines for agent in message_agents(line)],
    )
    sent = await deliveries_of(uow, [line.id for line in lines])
    return [
        message_response(
            line,
            channel,
            reader_id=caller.user_id,
            names=names,
            agents=agents,
            deliveries=sent.get(line.id, ()),
        )
        for line in lines
    ]


async def deliveries_of(
    uow: SqlAlchemyUnitOfWork, message_ids: Sequence[UUID]
) -> dict[UUID, list[Delivery]]:
    """Where these lines went outside Lemma, by line: one read for a page.
    Pushes to members are not among them."""
    by_line: dict[UUID, list[Delivery]] = {}
    for delivery in await DeliveryRepository(uow.session).of_messages(
        message_ids, to_places=True
    ):
        by_line.setdefault(delivery.message_id, []).append(delivery)
    return by_line


async def lines_across(
    uow: SqlAlchemyUnitOfWork,
    caller: Caller,
    found: Sequence[tuple[Channel, ChannelMessage]],
) -> list[tuple[ChannelRef, ChannelMessageResponse]]:
    """Lines of many channels, each with the channel it is in."""
    names, agents = await _named(
        uow,
        caller.pod_id,
        people=[
            *(person for _, line in found for person in message_people(line)),
            *(person for channel, _ in found for person in channel_people(channel)),
        ],
        agents=[agent for _, line in found for agent in message_agents(line)],
    )
    shown: list[tuple[ChannelRef, ChannelMessageResponse]] = []
    for channel, line in found:
        person = (
            Principal.member(channel.member_user_id)
            if channel.member_user_id
            else Principal.contact(channel.contact_id)
            if channel.contact_id
            else None
        )
        shown.append(
            (
                ChannelRef(
                    id=channel.id,
                    kind=channel.kind,
                    title=channel.title,
                    person=ref(person, names) if person else None,
                ),
                message_response(
                    line, channel, reader_id=caller.user_id, names=names, agents=agents
                ),
            )
        )
    return shown
