"""The wire shapes of a pod's channels -- the channel, its members, its
places -- and the mappers to them.

Everybody is named one way, a ``PersonRef``: a member of the pod, one of its
contacts, or one of its agents. A PATCH changes exactly the fields it names:
absent is unchanged, an explicit ``null`` clears.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
)

from app.modules.channels.contracts.people import PersonRef
from app.modules.channels.contracts.source_settings import SourceSettingsResponse
from app.modules.channels.domain.entities import (
    AgentAnswers,
    Channel,
    ChannelKind,
    ContactStatus,
    Member,
    MemberRole,
    NotifyLevel,
    Principal,
    SenderKind,
    Source,
    SourceKind,
    Visibility,
)
from app.modules.channels.infrastructure.inbox import UNREAD_CAP, Unread

#: A name lookup: what to call each person a page names.
Names = Mapping[Principal, str | None]
#: What to call each agent a page names.
AgentNames = Mapping[UUID, str]


def no_nul(value: str) -> str:
    """Text a person sends is stored as Postgres text, which cannot hold a NUL
    character: refused here, as the request's fault, not the database's."""
    if "\x00" in value:
        raise ValueError("Text cannot contain a NUL character")
    return value


#: Any text a person sends: kept whole, but never with a NUL in it.
UserText = Annotated[str, AfterValidator(no_nul)]

Title = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
    AfterValidator(no_nul),
]
Topic = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=500),
    AfterValidator(no_nul),
]


def _upper(value: object) -> object:
    return value.strip().upper() if isinstance(value, str) else value


#: Where a member may ask to be told outside the app: email, or a platform.
Platform = Annotated[
    Literal["EMAIL", "SLACK", "TEAMS", "WHATSAPP", "TELEGRAM", "RESEND"],
    BeforeValidator(_upper),
]
#: A line's number, as Postgres keeps it (``bigint``).
MAX_SEQ = 2**63 - 1


# -- reading --------------------------------------------------------------------


class SourceSummary(BaseModel):
    platform: str
    title: str | None = None


class ChannelSourceResponse(BaseModel):
    id: UUID
    platform: str = Field(
        description="Where it is: TELEGRAM, WHATSAPP, SLACK, TEAMS, RESEND, WEB..."
    )
    kind: SourceKind
    is_private: bool = Field(description="The platform keeps it to its members.")
    title: str | None = None
    surface_id: UUID | None = None
    last_inbound_at: datetime | None = None
    pending: bool = Field(
        default=False,
        description="A group the bot asked the platform for, not confirmed yet.",
    )


class UnreadResponse(BaseModel):
    lines: int = Field(
        default=0,
        description=(
            f"New lines since the reader last read it, in channels they joined "
            f"-- and, in one they are not in, while a line naming them is "
            f"unread, from that line; stops at {UNREAD_CAP}."
        ),
    )
    mentions: int = Field(default=0, description="Unread lines naming the reader.")
    waiting: int = Field(
        default=0, description="Questions here waiting on the reader's answer."
    )
    read_seq: int = Field(default=0, description="The last line the reader read.")


class AttentionResponse(BaseModel):
    since: datetime
    reason: str | None = None


class LinePreview(BaseModel):
    id: UUID
    seq: int
    sender: PersonRef
    text: str = Field(
        description="The start of what was said; empty when withheld or deleted."
    )
    withheld: bool = False
    deleted: bool = Field(default=False, description="Its writer deleted it.")
    created_at: datetime


class ChannelResponse(BaseModel):
    id: UUID
    kind: ChannelKind
    visibility: Visibility
    title: str | None = None
    topic: str | None = None
    person: PersonRef | None = Field(
        default=None,
        description=(
            "Whose channel it is: the member's for a DIRECT one, the contact's "
            "for a CONTACT one; absent for a GROUP."
        ),
    )
    archived: bool = False
    joined: bool = Field(
        default=False,
        description=(
            "Whether the reader is in it. A pod admin listing a bot's groups "
            "(`source_surface`) is shown its private ones too, not joined and "
            "without their last line, until they join."
        ),
    )
    created_at: datetime
    last_line_at: datetime
    last_seq: int = 0
    answers_shared: bool | None = Field(
        default=None,
        description=(
            "A group's: whether an answer an agent gives a member here with "
            "their own access is read by everybody in it (the default), or by "
            "that member alone until they share it. Absent for another kind."
        ),
    )
    # -- a contact's channel: the inbox -------------------------------------
    agents_answer: bool | None = Field(
        default=None,
        description="A contact's channel: whether its agents answer the contact.",
    )
    hand_back_after_hours: int | None = Field(
        default=None,
        description=(
            "A contact's channel: how long the assignee holds the contact after "
            "their last line before the agent answers again."
        ),
    )
    case_id: UUID | None = Field(
        default=None,
        description=(
            "A contact's channel: its current case, the one `status`, "
            "`assignee` and `attention` are read from (`/cases` lists them all)."
        ),
    )
    assignee: PersonRef | None = Field(
        default=None, description="A contact's channel: who answers them by hand."
    )
    status: ContactStatus | None = Field(
        default=None, description="A contact's channel: where it stands."
    )
    attention: AttentionResponse | None = Field(
        default=None,
        description=(
            "A contact's channel: its current case asks for a person, and why. "
            "Never set on another kind of channel."
        ),
    )
    sources: list[SourceSummary] = Field(
        default_factory=list, description="The outside places feeding it."
    )
    unread: UnreadResponse = Field(default_factory=UnreadResponse)
    last_line: LinePreview | None = None
    source_settings: SourceSettingsResponse | None = Field(
        default=None,
        description=(
            "Listed by `source_surface`: that bot's settings for its place here "
            "-- who answers its people outside the pod, and whether they are."
        ),
    )


class ChannelMemberResponse(BaseModel):
    person: PersonRef = Field(description="A member, a contact, or an agent.")
    role: MemberRole
    answers: AgentAnswers | None = Field(
        default=None, description="An agent's: when it answers here."
    )
    joined_at: datetime | None = None


class MyMembershipResponse(BaseModel):
    joined: bool = False
    role: MemberRole | None = None
    notify: NotifyLevel = NotifyLevel.ALL
    notify_via: list[str] | None = Field(
        default=None,
        description="Where lines reach them outside the app; absent for the pod's rule.",
    )
    muted_until: datetime | None = None
    read_seq: int = 0
    can_manage: bool = Field(
        default=False,
        description="Whether the reader may change this channel and its members.",
    )


class ChannelDetailResponse(ChannelResponse):
    opted_out: bool | None = Field(
        default=None,
        description=(
            "A contact's channel: whether they asked the pod to stop writing "
            "to a handle of theirs -- lines to it are not sent until they "
            "write from it again (the contact's `identities` say which)."
        ),
    )
    source_places: list[ChannelSourceResponse] = Field(default_factory=list)
    members: list[ChannelMemberResponse] = Field(default_factory=list)
    me: MyMembershipResponse = Field(default_factory=MyMembershipResponse)


class ChannelMemberListResponse(BaseModel):
    items: list[ChannelMemberResponse]
    next_cursor: str | None = Field(
        default=None, description="Pass as `cursor` for the next page."
    )


class ChannelListResponse(BaseModel):
    items: list[ChannelResponse]
    next_cursor: str | None = Field(
        default=None, description="Pass as `cursor` for the next page."
    )


class ReadResponse(BaseModel):
    read_seq: int


# -- writing --------------------------------------------------------------------


class AgentSeat(BaseModel):
    id: UUID = Field(description="One of the pod's agents.")
    answers: AgentAnswers = AgentAnswers.MENTIONED


class ChannelCreateRequest(BaseModel):
    title: Title
    topic: Topic | None = None
    visibility: Visibility = Visibility.PUBLIC
    members: list[UUID] = Field(
        default_factory=list, max_length=200, description="Members to add, by user id."
    )
    agents: list[AgentSeat] = Field(default_factory=list, max_length=50)


class ChannelUpdateRequest(BaseModel):
    """Absent is unchanged; ``null`` clears where clearing means something.

    A group takes ``title``, ``topic``, ``visibility``, ``archived``,
    ``answers_shared``. A contact's channel takes ``agents_answer``, ``hand_back_after_hours``,
    ``status``, ``assignee`` (and needs ``contact.message``). Anything else
    for the kind is 409.
    """

    title: Title | None = None
    topic: Topic | None = None
    visibility: Visibility | None = None
    archived: bool | None = None
    answers_shared: bool | None = Field(
        default=None,
        description=(
            "A group's: on, agents' answers made with the asker's access are "
            "read by everybody here; off, by the asker alone until they share "
            "one. Applies to answers posted from then on."
        ),
    )
    agents_answer: bool | None = Field(
        default=None,
        description="On also hands the contact back now: nobody is assigned.",
    )
    hand_back_after_hours: int | None = Field(default=None, ge=1, le=720)
    status: ContactStatus | None = None
    assignee: UUID | Literal["me"] | None = Field(
        default=None,
        description="`me`, or a member's user id; `null` unassigns.",
    )


#: The fields of a PATCH each kind of channel takes.
GROUP_FIELDS = frozenset({"title", "topic", "visibility", "archived", "answers_shared"})
CONTACT_FIELDS = frozenset(
    {"agents_answer", "hand_back_after_hours", "status", "assignee"}
)
#: Fields whose ``null`` is refused: they have no "nothing".
NOT_CLEARABLE = frozenset(
    {
        "title",
        "visibility",
        "archived",
        "answers_shared",
        "agents_answer",
        "hand_back_after_hours",
        "status",
    }
)


class MembersAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    people: list[UUID] = Field(
        default_factory=list, max_length=200, description="Members, by user id."
    )
    agents: list[AgentSeat] = Field(default_factory=list, max_length=50)


class MemberUpdateRequest(BaseModel):
    role: MemberRole | None = Field(default=None, description="A person's.")
    answers: AgentAnswers | None = Field(default=None, description="An agent's.")


class MyMembershipUpdate(BaseModel):
    """Absent is unchanged; ``null`` clears ``notify_via`` (the pod's rule)
    and ``muted_until`` (unmuted)."""

    notify: NotifyLevel | None = None
    notify_via: list[Platform] | None = Field(default=None, max_length=10)
    muted_until: datetime | None = None


class ReadRequest(BaseModel):
    seq: int = Field(
        ge=0, le=MAX_SEQ, description="The newest line the reader has seen."
    )
    thread_root_id: UUID | None = Field(
        default=None,
        description=(
            "Read in this thread: its replies (and its first line) up to `seq` "
            "are seen. Without it, the timeline is: thread replies are not."
        ),
    )


# -- mappers --------------------------------------------------------------------


def ref(principal: Principal, names: Names) -> PersonRef:
    if principal.user_id is not None:
        return PersonRef(
            kind=SenderKind.MEMBER, id=principal.user_id, name=names.get(principal)
        )
    return PersonRef(
        kind=SenderKind.CONTACT, id=principal.contact_id, name=names.get(principal)
    )


def agent_ref(agent_id: UUID, agents: AgentNames) -> PersonRef:
    return PersonRef(kind=SenderKind.AGENT, id=agent_id, name=agents.get(agent_id))


def channel_people(channel: Channel) -> list[Principal]:
    """Everybody a channel's own row names."""
    people: list[Principal] = []
    if channel.member_user_id:
        people.append(Principal.member(channel.member_user_id))
    if channel.contact_id:
        people.append(Principal.contact(channel.contact_id))
    if channel.assignee_user_id:
        people.append(Principal.member(channel.assignee_user_id))
    return people


def _person(channel: Channel, names: Names) -> PersonRef | None:
    if channel.member_user_id:
        return ref(Principal.member(channel.member_user_id), names)
    if channel.contact_id:
        return ref(Principal.contact(channel.contact_id), names)
    return None


def _inbox_fields(channel: Channel, names: Names) -> dict[str, object]:
    """A contact's inbox fields, read from its current case."""
    if channel.kind is not ChannelKind.CONTACT:
        return {}
    attention = (
        AttentionResponse(since=channel.attention_at, reason=channel.attention_reason)
        if channel.attention_at
        else None
    )
    return {
        "agents_answer": channel.agents_answer,
        "hand_back_after_hours": channel.hand_back_after_hours,
        "case_id": channel.case_id,
        "assignee": (
            ref(Principal.member(channel.assignee_user_id), names)
            if channel.assignee_user_id
            else None
        ),
        "status": channel.status,
        "attention": attention,
    }


def channel_response(
    channel: Channel,
    *,
    names: Names | None = None,
    unread: Unread | None = None,
    sources: list[Source] | None = None,
    last_line: LinePreview | None = None,
) -> ChannelResponse:
    names = names or {}
    return ChannelResponse(
        id=channel.id,
        kind=channel.kind,
        visibility=channel.visibility,
        title=channel.title,
        topic=channel.topic,
        person=_person(channel, names),
        archived=channel.is_archived,
        joined=unread.joined if unread else False,
        created_at=channel.created_at,
        last_line_at=channel.last_line_at,
        last_seq=channel.last_seq,
        answers_shared=(
            channel.answers_shared if channel.kind is ChannelKind.GROUP else None
        ),
        sources=[
            SourceSummary(platform=source.platform, title=source.title)
            for source in sources or []
        ],
        unread=(
            UnreadResponse(
                lines=unread.lines,
                mentions=unread.mentions,
                waiting=unread.waiting,
                read_seq=unread.read_seq,
            )
            if unread
            else UnreadResponse()
        ),
        last_line=last_line,
    ).model_copy(update=_inbox_fields(channel, names))


def source_response(source: Source) -> ChannelSourceResponse:
    return ChannelSourceResponse(
        id=source.id,
        platform=source.platform,
        kind=source.kind,
        is_private=source.is_private,
        title=source.title,
        surface_id=source.surface_id,
        last_inbound_at=source.last_inbound_at,
        pending=source.is_pending,
    )


def member_response(
    member: Member, *, names: Names, agents: AgentNames
) -> ChannelMemberResponse | None:
    if member.agent_id is not None:
        person = agent_ref(member.agent_id, agents)
    elif member.principal is not None:
        person = ref(member.principal, names)
    else:
        return None
    return ChannelMemberResponse(
        person=person,
        role=member.role,
        answers=member.answers,
        joined_at=member.joined_at,
    )


def my_membership(
    seat: Member | None, *, read_seq: int, can_manage: bool
) -> MyMembershipResponse:
    if seat is None:
        return MyMembershipResponse(read_seq=read_seq, can_manage=can_manage)
    return MyMembershipResponse(
        joined=True,
        role=seat.role,
        notify=seat.notify,
        notify_via=list(seat.notify_via) if seat.notify_via is not None else None,
        muted_until=seat.muted_until,
        read_seq=read_seq,
        can_manage=can_manage,
    )
