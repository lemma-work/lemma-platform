"""What other modules may ask of channels.

A channel is a pod's shared place; a line is posted to it once (``post``),
and everything that follows -- who is told, where it goes, which agents answer
-- is decided here, not by the caller. Places (``contracts/places``), lines
read for runs (``contracts/lines``), questions (``contracts/questions``) and
deliveries (``contracts/deliveries``) are submodules.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import datetime
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.access import Viewer, may_see
from app.modules.channels.domain.entities import (
    AccountKey,
    AgentAnswers,
    Asker,
    Channel,
    ChannelKind,
    ChannelMessage,
    ContactStatus,
    Delivery,
    DeliveryReasonCode,
    DeliveryStatus,
    ExternalAccount,
    Member,
    MemberRole,
    NotifyLevel,
    Principal,
    Question,
    QuestionAsker,
    QuestionKind,
    QuestionState,
    SeatedBy,
    Sender,
    SenderKind,
    Source,
    SourceKind,
    Visibility,
)
from app.modules.channels.domain.idempotency import keyed_client_id
from app.modules.channels.domain.parts import (
    MAX_PARTS,
    FilePart,
    LinkPart,
    MentionPart,
    Part,
)
from app.modules.channels.domain.projection import (
    Reader,
    may_read,
    readable,
    withheld_from,
)
from app.modules.channels.infrastructure.cases import CaseRepository
from app.modules.channels.infrastructure.lines import LineRepository, NewLine
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.names import Names
from app.modules.channels.infrastructure.questions import NewQuestion
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.source_repository import SourceRepository
from app.modules.channels.infrastructure.speakers import (
    MAX_SPEAKERS,
    Speaker,
    SpeakerReads,
)
from app.modules.channels.services.messages import (
    LineRefused,
    MemberLines,
    MemberPost,
    Naming,
    erase_line,
    rewrite_line,
)
from app.modules.channels.services.places import (
    ChannelPlaces,
    ContactsHome,
    GroupHome,
    Home,
    MembersHome,
    Place,
)
from app.modules.channels.services.posting import ChannelNotFound, Post, Timeline

__all__ = [
    "MAX_PARTS",
    "AccountKey",
    "AgentAnswers",
    "Asker",
    "Channel",
    "ChannelKind",
    "ChannelMessage",
    "ChannelNotFound",
    "ContactStatus",
    "ContactsHome",
    "Delivery",
    "DeliveryReasonCode",
    "DeliveryStatus",
    "ExternalAccount",
    "FilePart",
    "GroupHome",
    "Home",
    "LineRefused",
    "LinkPart",
    "Member",
    "MemberRole",
    "MembersHome",
    "MentionPart",
    "NewLine",
    "NewQuestion",
    "NotifyLevel",
    "Part",
    "Place",
    "Principal",
    "Question",
    "QuestionAsker",
    "QuestionKind",
    "QuestionState",
    "Reader",
    "SeatedBy",
    "Sender",
    "SenderKind",
    "Source",
    "SourceKind",
    "Speaker",
    "Viewer",
    "Visibility",
    "assign",
    "assignee_of",
    "background",
    "call_for_person",
    "channel_by_id",
    "contact_channel",
    "direct_channel",
    "erase_line",
    "hand_back",
    "join",
    "keyed_client_id",
    "may_read",
    "may_see",
    "member_of",
    "acknowledge",
    "agent_names",
    "named_in",
    "names_of",
    "post",
    "read_upto",
    "readable",
    "revise_line",
    "source_by_id",
    "source_for",
    "sources_of",
    "speakers_in",
    "withheld_from",
    "write_as_member",
]


async def source_for(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, place: Place, home: Home
) -> Source:
    """The source for an outside place, with its channel made the first time."""
    return await ChannelPlaces(uow).source_for(pod_id=pod_id, place=place, home=home)


async def source_by_id(uow: SqlAlchemyUnitOfWork, source_id: UUID) -> Source | None:
    return await SourceRepository(uow.session).get(source_id)


async def sources_of(uow: SqlAlchemyUnitOfWork, channel_id: UUID) -> list[Source]:
    """A channel's places, the one heard from most recently first."""
    return await SourceRepository(uow.session).of_channel(channel_id)


async def channel_by_id(uow: SqlAlchemyUnitOfWork, channel_id: UUID) -> Channel | None:
    return await ChannelRepository(uow.session).get(channel_id)


async def direct_channel(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, user_id: UUID
) -> Channel:
    """The member's own channel with the pod, made -- with them in it -- if new."""
    return await ChannelRepository(uow.session).direct(pod_id=pod_id, user_id=user_id)


async def contact_channel(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, contact_id: UUID
) -> Channel:
    """The contact's channel, made -- with them in it -- if new."""
    return await ChannelRepository(uow.session).for_contact(
        pod_id=pod_id, contact_id=contact_id
    )


async def join(
    uow: SqlAlchemyUnitOfWork, *, channel: Channel, principal: Principal
) -> None:
    """Somebody is in the channel -- the platform shows they are in its place."""
    await MemberRepository(uow.session).add_person(
        channel, principal, seated_by=SeatedBy.PLATFORM
    )


async def member_of(
    uow: SqlAlchemyUnitOfWork, *, channel_id: UUID, principal: Principal
) -> Member | None:
    """Somebody's membership of a channel -- how they hear of it -- if they have one."""
    return await MemberRepository(uow.session).of_principal(channel_id, principal)


async def read_upto(
    uow: SqlAlchemyUnitOfWork, *, channel_id: UUID, user_id: UUID
) -> int:
    """How far a member has read the channel: the ``seq`` of the last line
    they read there, ``0`` if none."""
    return await MemberRepository(uow.session).read_seq(
        channel_id=channel_id, user_id=user_id
    )


async def post(
    uow: SqlAlchemyUnitOfWork,
    *,
    channel_id: UUID,
    line: NewLine,
    question: NewQuestion | None = None,
    mentions: Sequence[Principal] = (),
    mentioned_agents: Sequence[UUID] = (),
    deliver_to: Sequence[UUID] = (),
) -> ChannelMessage | None:
    """Add a line to the channel's timeline -- the same message once -- and
    whatever follows: who is told, where it goes, which agents answer.

    ``deliver_to``: the outside places to carry it to (a line written in
    Lemma); a line heard on a platform is already there. ``None`` for a line
    with nothing in it; ``ChannelNotFound`` when the channel is gone.
    """
    channel = await ChannelRepository(uow.session).get(channel_id)
    if channel is None:
        raise ChannelNotFound(str(channel_id))
    from app.core.authorization.delegation import effective_agent_id

    return await Timeline(uow).post(
        channel,
        Post(
            line=line,
            question=question,
            mentions=mentions,
            mentioned_agents=mentioned_agents,
            deliver_to=deliver_to,
            pod_agent_id=effective_agent_id(None, pod_id=channel.pod_id),
        ),
    )


async def revise_line(
    uow: SqlAlchemyUnitOfWork,
    *,
    message_id: UUID,
    text: str,
    parts: Sequence[Part],
    mentions: Sequence[Principal] = (),
    mentioned_agents: Sequence[UUID] = (),
) -> ChannelMessage | None:
    """A line said again differently -- a platform's edit of a message heard
    there: its words and parts, and whom it names now (``named_in`` of the
    new parts). ``None`` when the line is gone or deleted."""
    return await rewrite_line(
        uow,
        message_id=message_id,
        text=text,
        parts=parts,
        people=mentions,
        agents=mentioned_agents,
    )


async def named_in(
    uow: SqlAlchemyUnitOfWork, *, channel_id: UUID, parts: Sequence[Part]
) -> tuple[list[Principal], list[UUID]]:
    """Whom these parts name that may be told of a line in this channel -- the
    people who can see it -- and the agents they name. Anybody else named
    keeps their name on the line and is not told: a platform's words are
    what was said there, not a request to show them this channel."""
    channel = await ChannelRepository(uow.session).get(channel_id)
    if channel is None:
        return [], []
    named = await Naming(uow).of(channel, parts)
    return named.people, named.agents


async def names_of(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, people: Collection[Principal]
) -> dict[Principal, str | None]:
    """What the pod calls these people: a member's name, a contact's."""
    return await Names(uow).of(pod_id=pod_id, people=people)


async def agent_names(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, agent_ids: Collection[UUID]
) -> dict[UUID, str]:
    """The pod's agents among these, by name."""
    return await Names(uow).agents(pod_id=pod_id, agent_ids=agent_ids)


async def write_as_member(
    uow: SqlAlchemyUnitOfWork,
    *,
    channel_id: UUID,
    user_id: UUID,
    text: str,
    source_id: UUID | None = None,
    client_message_id: str | None = None,
) -> ChannelMessage:
    """A member's line, by the rules a line written in the app is held to,
    carried to ``source_id`` (one of the channel's places). Raises
    ``LineRefused`` with the reason for the member; ``ChannelNotFound``."""
    channel = await ChannelRepository(uow.session).get(channel_id)
    if channel is None:
        raise ChannelNotFound(str(channel_id))
    return await MemberLines(uow).post(
        channel,
        user_id=user_id,
        post=MemberPost(
            text=text, source_id=source_id, client_message_id=client_message_id
        ),
    )


async def background(
    uow: SqlAlchemyUnitOfWork, *, channel_id: UUID, reader: Reader, limit: int
) -> list[ChannelMessage]:
    """The recent lines a run reading as ``reader`` may be shown, oldest first.
    A contact reads only their own place, so only that place is read."""
    if not reader.is_member and reader.source_id is None:
        return []
    recent = await LineRepository(uow.session).page(
        channel_id,
        limit=limit,
        source_id=None if reader.is_member else reader.source_id,
    )
    return readable(reader, recent)


async def call_for_person(
    uow: SqlAlchemyUnitOfWork, *, channel_id: UUID, reason: str | None
) -> bool:
    """A contact's channel asks for a person, until somebody is assigned;
    another channel has no case to ask in, and is left as it is. Whether
    the channel had one."""
    return await CaseRepository(uow.session).call_for_person(channel_id, reason=reason)


async def assign(
    uow: SqlAlchemyUnitOfWork, *, channel_id: UUID, user_id: UUID | None
) -> None:
    """Somebody answers this contact by hand now -- or, with ``None``, nobody:
    its agent answers them again."""
    await CaseRepository(uow.session).assign(channel_id, user_id)


async def hand_back(
    uow: SqlAlchemyUnitOfWork,
    *,
    channel_id: UUID,
    user_id: UUID,
    quiet_since: datetime,
) -> bool:
    """Let go of ``user_id`` as the contact's assignee if they have not
    written since ``quiet_since``: one conditional write, so it happens once
    and never to a member who just wrote. Whether this call let them go."""
    return await CaseRepository(uow.session).hand_back(
        channel_id, user_id=user_id, quiet_since=quiet_since
    )


async def acknowledge(uow: SqlAlchemyUnitOfWork, channel_id: UUID) -> bool:
    """Claim the contact's one acknowledgement of this wait -- until a member
    writes to them or the case is closed. Whether it is this caller's to
    send."""
    return await CaseRepository(uow.session).acknowledge(channel_id)


async def assignee_of(uow: SqlAlchemyUnitOfWork, channel_id: UUID) -> UUID | None:
    channel = await ChannelRepository(uow.session).get(channel_id)
    return channel.assignee_user_id if channel else None


async def speakers_in(
    uow: SqlAlchemyUnitOfWork,
    channel_ids: Collection[UUID],
    *,
    limit: int = MAX_SPEAKERS,
) -> list[Speaker]:
    """Who spoke in these channels, once per channel, most recent first."""
    return await SpeakerReads(uow.session).speakers(channel_ids, limit=limit)
