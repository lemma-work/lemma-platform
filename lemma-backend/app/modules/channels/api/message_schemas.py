"""The wire shapes of a channel's lines, the questions they ask, their
deliveries, the inbox and search -- and the mappers to them.

A line is always shown as one member reads it (``message_response``): an
answer an agent made with another member's own access is ``withheld`` -- the
reader sees that it was given, and for whom, not what it said.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.modules.channels.api.schemas import (
    AgentNames,
    ChannelResponse,
    Names,
    PersonRef,
    UserText,
    agent_ref,
    no_nul,
    ref,
)
from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Delivery,
    DeliveryStatus,
    Principal,
    QuestionKind,
    QuestionState,
    SenderKind,
)
from app.modules.channels.domain.idempotency import client_name_of
from app.modules.channels.domain.parts import MAX_PARTS, MentionPart, Part
from app.modules.channels.domain.projection import (
    asker_only,
    shareable_by,
    withheld_from,
)

#: How much of a line a preview shows.
PREVIEW_CHARS = 140

JsonObject = dict[str, object]


class LineDelivery(BaseModel):
    """Where a line went outside Lemma, as everybody who reads it is shown."""

    id: UUID = Field(description="The delivery; retry it by this id.")
    source_id: UUID | None = None
    platform: str | None = None
    status: DeliveryStatus
    reason: str | None = Field(
        default=None, description="Why it was skipped, failed or given up."
    )


class QuestionResponse(BaseModel):
    kind: QuestionKind
    state: QuestionState
    question: JsonObject = Field(
        description=(
            "What is asked, in the asker's shape: a `prompt`, and `choices` or "
            "a form `schema` where it offers them."
        )
    )
    awaits: PersonRef = Field(description="The one person who may answer it.")
    answer: JsonObject | None = None
    answered_by: PersonRef | None = None
    answered_at: datetime | None = None
    expires_at: datetime | None = None


class ChannelMessageResponse(BaseModel):
    id: UUID
    channel_id: UUID
    seq: int = Field(description="Its place in the channel's timeline.")
    sender: PersonRef
    on_behalf_of: PersonRef | None = Field(
        default=None,
        description=(
            "For an agent's line: the member or contact it acted for, whose "
            "access the answer was made with."
        ),
    )
    text: str | None = Field(
        default=None,
        description="Absent where the line is withheld from the reader, or deleted.",
    )
    withheld: bool = Field(
        default=False,
        description=(
            "An answer an agent made with another member's own access: the "
            "reader sees that it was given, and for whom, not what it said."
        ),
    )
    visibility: Literal["asker_only", "channel"] = Field(
        default="channel",
        description=(
            "`asker_only`: an agent's answer made with one member's own access, "
            "read in full by that member alone; everybody else here is shown "
            "it `withheld`. `channel`: everybody here reads it."
        ),
    )
    can_share: bool = Field(
        default=False,
        description=(
            "The reader is the member an `asker_only` answer was made for, and "
            "may share it with the channel (`…/messages/{id}/share`)."
        ),
    )
    shared_at: datetime | None = Field(
        default=None,
        description=(
            "When it was shared with the channel: by the member it was made "
            "for, or as it was posted, in a group that shows agents' answers "
            "to everybody (`answers_shared`)."
        ),
    )
    parts: list[Part] = Field(default_factory=list)
    client_message_id: str | None = Field(
        default=None,
        description=(
            "On the reader's own line: the name their client gave it, as sent "
            "(`client_message_id`, or `Idempotency-Key`)."
        ),
    )
    source_id: UUID | None = Field(
        default=None,
        description="Where it was said or sent; absent for a line only in Lemma.",
    )
    members_only: bool = False
    conversation_id: UUID | None = None
    reply_to_id: UUID | None = None
    thread_root_id: UUID | None = Field(
        default=None, description="The line this one is a reply in the thread of."
    )
    reply_count: int = Field(default=0, description="On a thread's first line.")
    last_reply_at: datetime | None = None
    edited_at: datetime | None = None
    deleted: bool = False
    removed_by: PersonRef | None = Field(
        default=None,
        description=(
            "On a deleted line: the member who removed it, when that was not "
            "who wrote it -- somebody who may change the channel."
        ),
    )
    question: QuestionResponse | None = None
    delivered: list[LineDelivery] | None = Field(
        default=None,
        description=(
            "Each place outside Lemma the line went to, and what became of it "
            "-- on every line the reader may read. Pushes to members are not "
            "listed here (`…/deliveries` has the writer's every send)."
        ),
    )
    created_at: datetime


class ChannelMessagesResponse(BaseModel):
    items: list[ChannelMessageResponse] = Field(description="Oldest first.")
    has_more: bool = Field(
        description=(
            "Whether there are more lines in the direction read: older ones, or "
            "with `after_seq`, newer ones."
        )
    )
    next_before_seq: int | None = Field(
        default=None, description="Pass as `before_seq` for the older page."
    )
    next_after_seq: int | None = Field(
        default=None, description="Pass as `after_seq` for the newer page."
    )


class ThreadResponse(BaseModel):
    root: ChannelMessageResponse
    items: list[ChannelMessageResponse] = Field(description="Replies, oldest first.")
    has_more: bool = Field(
        description=(
            "Whether there are more replies in the direction read: newer ones, "
            "or with `before_seq` or `newest`, older ones."
        )
    )
    next_after_seq: int | None = Field(
        default=None, description="Pass as `after_seq` for the newer page."
    )
    next_before_seq: int | None = Field(
        default=None,
        description="Reading backwards: pass as `before_seq` for the older page.",
    )


def _parts_without_nul(parts: list[Part] | None) -> list[Part] | None:
    """Every word of a part is stored, so none may hold a NUL either."""
    for part in parts or ():
        for value in part.model_dump().values():
            if isinstance(value, str):
                no_nul(value)
    return parts


class ChannelPostRequest(BaseModel):
    text: UserText = Field(default="", max_length=20_000)
    members_only: bool = Field(
        default=False, description="A note to the pod's members: never sent outside."
    )
    source_id: UUID | None = Field(
        default=None,
        description=(
            "Which outside place it goes to; by default the thread's, else the "
            "one the channel last heard from."
        ),
    )
    thread_root_id: UUID | None = Field(
        default=None, description="Reply in the thread of this line."
    )
    reply_to_id: UUID | None = None
    parts: list[Part] = Field(
        default_factory=list,
        max_length=MAX_PARTS,
        description="Mentions, files and links. Who a line names is read from here.",
    )
    client_message_id: UserText | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description=(
            "The client's name for this line: sending it again is the same "
            "line. Shown back on the line to its writer, as sent."
        ),
    )

    check_parts = field_validator("parts")(_parts_without_nul)


class ChannelEditRequest(BaseModel):
    """Absent is unchanged; ``null`` clears the parts. The text cannot be
    cleared: delete the line instead."""

    text: UserText | None = Field(default=None, min_length=1, max_length=20_000)
    parts: list[Part] | None = Field(
        default=None,
        max_length=MAX_PARTS,
        description="Absent keeps the parts; `null` or `[]` clears them.",
    )

    check_parts = field_validator("parts")(_parts_without_nul)


class ChannelAnswerRequest(BaseModel):
    answer: Annotated[
        JsonObject,
        Field(
            description=(
                "The answer, in the question's terms: `{choice}` or `{text}` for "
                "a question, `{decision, note?}` for an approval, the form's "
                "fields for a form."
            )
        ),
    ]


class DeliveryTarget(BaseModel):
    kind: Literal["source", "member"]
    source_id: UUID | None = None
    person: PersonRef | None = Field(
        default=None, description="The member a push went to."
    )


class DeliveryResponse(BaseModel):
    id: UUID
    message_id: UUID
    target: DeliveryTarget
    platform: str | None = None
    status: DeliveryStatus
    reason: str | None = None
    attempts: int = 0
    next_attempt_at: datetime | None = None
    sent_at: datetime | None = None
    created_at: datetime | None = None


class DeliveriesResponse(BaseModel):
    items: list[DeliveryResponse]


class ChannelRef(BaseModel):
    id: UUID
    kind: ChannelKind
    title: str | None = None
    person: PersonRef | None = None


class InboxItem(BaseModel):
    channel: ChannelRef
    message: ChannelMessageResponse


class InboxTotals(BaseModel):
    channels: int = Field(description="Channels with something new.")
    mentions: int = Field(description="Unread lines naming the reader.")
    waiting: int = Field(description="Questions waiting on the reader's answer.")
    contacts: int = Field(
        default=0,
        description=(
            "Contacts waiting on a reply: asking for a person, or assigned to "
            "the reader and written to last by the contact."
        ),
    )


class InboxResponse(BaseModel):
    totals: InboxTotals
    waiting: list[InboxItem] = Field(
        default_factory=list,
        description="Questions waiting on the reader, newest first.",
    )
    mentions: list[InboxItem] = Field(
        default_factory=list,
        description="Unread lines naming the reader, newest first.",
    )
    contacts: list[ChannelResponse] = Field(
        default_factory=list,
        description=(
            "Contacts waiting on a reply, most recently active first: those "
            "asking for a person, and those assigned to the reader the contact "
            "wrote to last. Only for a reader who may see contacts."
        ),
    )


class SearchItem(InboxItem):
    rank: float = Field(description="How closely the line matches the words.")
    snippet: str | None = Field(
        default=None,
        description="The words around the match, as plain text.",
    )


class SearchResponse(BaseModel):
    items: list[SearchItem]
    next_cursor: str | None = None


# -- mappers --------------------------------------------------------------------


def message_people(message: ChannelMessage) -> list[Principal]:
    """Everybody a line names, for one name lookup per page."""
    people: list[Principal] = []
    sender = message.sender.principal
    if sender is not None:
        people.append(sender)
    if message.sender.on_behalf_of:
        people.append(message.sender.on_behalf_of)
    if message.removed_by is not None:
        people.append(Principal.member(message.removed_by))
    question = message.question
    if question is not None:
        people.append(question.awaits)
        if question.answered_by is not None:
            people.append(question.answered_by)
    for part in message.parts:
        if isinstance(part, MentionPart) and part.kind == "MEMBER":
            people.append(Principal.member(part.id))
        elif isinstance(part, MentionPart) and part.kind == "CONTACT":
            people.append(Principal.contact(part.id))
    return people


def message_agents(message: ChannelMessage) -> list[UUID]:
    named = [
        part.id
        for part in message.parts
        if isinstance(part, MentionPart) and part.kind == "AGENT"
    ]
    return ([message.sender.agent_id] if message.sender.agent_id else []) + named


def _parts(message: ChannelMessage, names: Names, agents: AgentNames) -> list[Part]:
    """The line's parts, each mention called what its person or agent is
    called now -- a writer that sent only an id still shows a name."""
    shown: list[Part] = []
    for part in message.parts:
        if isinstance(part, MentionPart):
            current = (
                agents.get(part.id)
                if part.kind == "AGENT"
                else names.get(
                    Principal.member(part.id)
                    if part.kind == "MEMBER"
                    else Principal.contact(part.id)
                )
            )
            if current and current != part.name:
                part = part.model_copy(update={"name": current[:255]})
        shown.append(part)
    return shown


def _sender(message: ChannelMessage, names: Names, agents: AgentNames) -> PersonRef:
    sender = message.sender
    principal = sender.principal
    if principal is not None:
        named = ref(principal, names)
        return named.model_copy(update={"name": named.name or sender.display_name})
    if sender.agent_id is not None:
        # The pod's own assistant (its id is the pod's) is always called what
        # the pod is called now -- not whatever name its line was written with.
        own = sender.agent_id == message.pod_id
        return PersonRef(
            kind=sender.kind,
            id=sender.agent_id,
            name=(agents.get(sender.agent_id) or sender.display_name)
            if own
            else (sender.display_name or agents.get(sender.agent_id)),
        )
    return PersonRef(kind=sender.kind, id=None, name=sender.display_name)


def _question(message: ChannelMessage, names: Names) -> QuestionResponse | None:
    question = message.question
    if question is None:
        return None
    return QuestionResponse(
        kind=question.kind,
        state=question.state,
        question={k: v for k, v in question.question.items() if k != "ref"},
        awaits=ref(question.awaits, names),
        answer=dict(question.answer) if question.answer is not None else None,
        answered_by=ref(question.answered_by, names) if question.answered_by else None,
        answered_at=question.answered_at,
        expires_at=question.expires_at,
    )


def hidden_from(message: ChannelMessage, channel: Channel, reader_id: UUID) -> bool:
    return withheld_from(
        message,
        user_id=reader_id,
        in_their_direct=(
            channel.kind is ChannelKind.DIRECT and channel.member_user_id == reader_id
        ),
    )


def message_response(
    message: ChannelMessage,
    channel: Channel,
    *,
    reader_id: UUID,
    names: Names | None = None,
    agents: AgentNames | None = None,
    deliveries: Sequence[Delivery] | None = None,
) -> ChannelMessageResponse:
    """One line as the member ``reader_id`` reads it. ``deliveries``: the
    line's, when it was read with them -- where it went outside Lemma is shown
    to everybody who may read it; a push to a member is that member's."""
    names = names or {}
    behalf = message.sender.on_behalf_of
    hidden = hidden_from(message, channel, reader_id)
    in_a_direct = channel.kind is ChannelKind.DIRECT
    return ChannelMessageResponse(
        id=message.id,
        channel_id=message.channel_id,
        seq=message.seq,
        sender=_sender(message, names, agents or {}),
        on_behalf_of=ref(behalf, names) if behalf else None,
        text=None if hidden or message.is_deleted else message.text,
        withheld=hidden,
        visibility=(
            "asker_only" if asker_only(message, in_a_direct=in_a_direct) else "channel"
        ),
        can_share=shareable_by(message, user_id=reader_id, in_a_direct=in_a_direct),
        shared_at=message.shared_at,
        parts=[] if hidden else _parts(message, names, agents or {}),
        client_message_id=client_name_of(message.client_message_id, user_id=reader_id),
        source_id=message.place_id,
        members_only=message.members_only,
        conversation_id=message.conversation_id,
        reply_to_id=message.reply_to_id,
        thread_root_id=message.thread_root_id,
        reply_count=message.reply_count,
        last_reply_at=message.last_reply_at,
        edited_at=message.edited_at,
        deleted=message.is_deleted,
        removed_by=(
            ref(Principal.member(message.removed_by), names)
            if message.removed_by is not None
            else None
        ),
        question=None if hidden else _question(message, names),
        delivered=(
            [
                LineDelivery(
                    id=delivery.id,
                    source_id=delivery.source_id,
                    platform=delivery.platform,
                    status=delivery.status,
                    reason=delivery.reason,
                )
                for delivery in deliveries
                if delivery.source_id is not None
            ]
            if deliveries is not None and not hidden
            else None
        ),
        created_at=message.created_at,
    )


def wrote(message: ChannelMessage, reader_id: UUID) -> bool:
    """Whether ``reader_id`` wrote the line: whose its sends are to see."""
    return (
        message.sender.kind is SenderKind.MEMBER and message.sender.user_id == reader_id
    )


def delivery_response(delivery: Delivery, *, names: Names) -> DeliveryResponse:
    if delivery.recipient_user_id is not None:
        person = Principal.member(delivery.recipient_user_id)
        target = DeliveryTarget(kind="member", person=ref(person, names))
    else:
        target = DeliveryTarget(kind="source", source_id=delivery.source_id)
    return DeliveryResponse(
        id=delivery.id,
        message_id=delivery.message_id,
        target=target,
        platform=delivery.platform,
        status=delivery.status,
        reason=delivery.reason,
        attempts=delivery.attempts,
        next_attempt_at=delivery.next_attempt_at,
        sent_at=delivery.sent_at,
        created_at=delivery.created_at,
    )


__all__ = [
    "ChannelAnswerRequest",
    "ChannelEditRequest",
    "ChannelMessageResponse",
    "ChannelMessagesResponse",
    "ChannelPostRequest",
    "ChannelRef",
    "DeliveriesResponse",
    "DeliveryResponse",
    "InboxItem",
    "InboxResponse",
    "InboxTotals",
    "QuestionResponse",
    "SearchResponse",
    "ThreadResponse",
    "agent_ref",
    "delivery_response",
    "hidden_from",
    "message_agents",
    "message_people",
    "message_response",
]
