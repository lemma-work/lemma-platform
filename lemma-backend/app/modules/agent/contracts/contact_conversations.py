"""A contact's conversations, for the module that forgets and exports contacts.

A contact's conversation is marked on its metadata (``domain/outsiders``), and
that mark is how these find them. Deleting cascades to its runs, messages,
waits, approvals and routing links; notifications that pointed at it lose the
pointer, which is why the caller redacts them first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import (
    ColumnElement,
    Select,
    delete,
    func,
    literal,
    select,
    tuple_,
    update,
)
from sqlalchemy.orm import aliased

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.outsiders import (
    AUDIENCE_KEY,
    CONTACT,
    CONTACT_KEY,
    HANDED_TO_KEY,
    OUTSIDERS,
    handed_to,
)
from app.modules.agent.domain.private_notes import is_private_note, run_is_private
from app.modules.agent.domain.value_objects import (
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.models.conversation import (
    AgentRunModel,
    ConversationModel,
    MessageModel,
)

#: The message kinds a person outside the pod may have been shown. Thinking and
#: tool traffic are the pod's working.
_SEEN_KINDS = (MessageKind.TEXT.value, MessageKind.NOTIFICATION.value)

#: The most conversations one page of an export carries.
MAX_EXPORTED_CONVERSATIONS = 20

#: The most messages one conversation contributes to an export.
MAX_EXPORTED_MESSAGES = 2000


class ExportedMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str
    text: str
    created_at: datetime
    sequence: int


#: Where a visitor's own message keeps the name its page gave it.
CLIENT_NONCE_KEY = "client_nonce"


class VisibleMessage(ExportedMessage):
    """A message as the visitor's page reads it back.

    ``client_nonce`` is the page's own name for a message it sent, so the page
    can match the server's copy to the one it already drew. It is the page's
    bookkeeping, not what was said, so an export leaves it out.
    """

    client_nonce: str | None = None


class ExportedConversation(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    title: str | None
    created_at: datetime
    messages: tuple[ExportedMessage, ...]


@dataclass(frozen=True, slots=True)
class ConversationCursor:
    """The last conversation one export page carried, in the export's order."""

    created_at: datetime
    conversation_id: UUID


@dataclass(frozen=True, slots=True)
class ConversationsPage:
    conversations: tuple[ExportedConversation, ...]
    #: Where the next page starts, or ``None`` when this was the last.
    next_after: ConversationCursor | None


def _theirs(pod_id: UUID, contact_id: UUID) -> tuple[ColumnElement[bool], ...]:
    # Containment, so the metadata's GIN index answers it.
    return (
        ConversationModel.pod_id == pod_id,
        ConversationModel.conversation_metadata.contains(
            {AUDIENCE_KEY: CONTACT, CONTACT_KEY: str(contact_id)}
        ),
    )


async def contact_conversation_ids(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    contact_id: UUID,
    after: UUID | None = None,
    limit: int = 500,
) -> list[UUID]:
    """A page of the conversations this pod had with this contact, by id."""
    query = select(ConversationModel.id).where(*_theirs(pod_id, contact_id))
    if after is not None:
        query = query.where(ConversationModel.id > after)
    return list(
        await uow.session.scalars(query.order_by(ConversationModel.id).limit(limit))
    )


async def forget_contact_conversations(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, contact_id: UUID
) -> int:
    """Delete every conversation this pod had with this contact."""
    result = await uow.session.execute(
        delete(ConversationModel).where(*_theirs(pod_id, contact_id))
    )
    return int(result.rowcount or 0)


async def export_contact_conversations(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    contact_id: UUID,
    after: ConversationCursor | None = None,
    limit: int = MAX_EXPORTED_CONVERSATIONS,
) -> ConversationsPage:
    """What was said with this contact, a page of conversations at a time.

    Text only. Tool calls and results are the pod's working, not the
    conversation the contact had, and may name things that were never theirs.
    A member's private note, and the answer it got, were never the contact's
    either, so neither is exported. Two reads a page, whatever its size: the
    conversations, then every message of all of them.
    """
    page_size = max(1, min(limit, MAX_EXPORTED_CONVERSATIONS))
    query = select(ConversationModel).where(*_theirs(pod_id, contact_id))
    if after is not None:
        query = query.where(
            tuple_(ConversationModel.created_at, ConversationModel.id)
            > tuple_(
                literal(after.created_at, ConversationModel.created_at.type),
                literal(after.conversation_id, ConversationModel.id.type),
            )
        )
    conversations = list(
        await uow.session.scalars(
            query.order_by(ConversationModel.created_at, ConversationModel.id).limit(
                page_size + 1
            )
        )
    )
    more = len(conversations) > page_size
    conversations = conversations[:page_size]
    said = await _messages_of(uow, [conversation.id for conversation in conversations])
    last = conversations[-1] if more else None
    return ConversationsPage(
        conversations=tuple(
            ExportedConversation(
                id=conversation.id,
                title=conversation.title,
                created_at=conversation.created_at,
                messages=said.get(conversation.id, ()),
            )
            for conversation in conversations
        ),
        next_after=(
            ConversationCursor(created_at=last.created_at, conversation_id=last.id)
            if last is not None
            else None
        ),
    )


def _seen_by_them() -> Select[tuple[MessageModel, dict[str, object] | None]]:
    """Messages a person outside the pod could have seen, with their run's metadata."""
    return (
        select(MessageModel, AgentRunModel.run_metadata.label("run_metadata"))
        .outerjoin(AgentRunModel, AgentRunModel.id == MessageModel.agent_run_id)
        .where(
            MessageModel.role.in_(("user", "assistant")),
            MessageModel.kind.in_(_SEEN_KINDS),
            MessageModel.text.is_not(None),
            MessageModel.tool_name.is_(None),
        )
    )


async def _messages_of(
    uow: SqlAlchemyUnitOfWork, conversation_ids: list[UUID]
) -> dict[UUID, tuple[ExportedMessage, ...]]:
    """The messages of several conversations in one read, each cut to the cap."""
    if not conversation_ids:
        return {}
    numbered = (
        _seen_by_them()
        .add_columns(
            func.row_number()
            .over(
                partition_by=MessageModel.conversation_id,
                order_by=MessageModel.sequence,
            )
            .label("place")
        )
        .where(MessageModel.conversation_id.in_(conversation_ids))
        .subquery()
    )
    seen = aliased(MessageModel, numbered)
    rows = await uow.session.execute(
        select(seen, numbered.c.run_metadata)
        .where(numbered.c.place <= MAX_EXPORTED_MESSAGES)
        .order_by(numbered.c.conversation_id, numbered.c.sequence)
    )
    grouped: dict[UUID, list[ExportedMessage]] = {}
    for row, run_metadata in rows:
        if _was_seen(row, run_metadata):
            grouped.setdefault(row.conversation_id, []).append(_exported(row))
    return {key: tuple(value) for key, value in grouped.items()}


async def visible_messages(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID, *, after: int, limit: int
) -> tuple[VisibleMessage, ...]:
    """What the person outside the pod saw of a conversation, after a point.

    Their words, the bot's answers and members' follow-ups, oldest first. Never
    a tool call, the model's thinking, another notification, a member's private
    note, or what a note's run said back.
    """
    rows = await uow.session.execute(
        _seen_by_them()
        .where(
            MessageModel.conversation_id == conversation_id,
            MessageModel.sequence > after,
        )
        .order_by(MessageModel.sequence)
        .limit(limit)
    )
    return tuple(
        VisibleMessage(
            role=message.role,
            text=message.text or "",
            created_at=message.created_at,
            sequence=message.sequence,
            client_nonce=_client_nonce(message),
        )
        for message, run_metadata in rows
        if _was_seen(message, run_metadata)
    )


def _exported(message: MessageModel) -> ExportedMessage:
    return ExportedMessage(
        role=message.role,
        text=message.text or "",
        created_at=message.created_at,
        sequence=message.sequence,
    )


def _was_seen(message: MessageModel, run_metadata: dict[str, object] | None) -> bool:
    return (
        _was_said_to_them(message)
        and not is_private_note(message.message_metadata)
        and not run_is_private(run_metadata)
    )


def _client_nonce(message: MessageModel) -> str | None:
    """The page's name for a message the visitor sent; nobody else's has one."""
    if message.role != "user":
        return None
    nonce = (message.message_metadata or {}).get(CLIENT_NONCE_KEY)
    return nonce if isinstance(nonce, str) else None


def _was_said_to_them(message: MessageModel) -> bool:
    """Text, or a member's follow-up that reached them -- never another notification."""
    if message.kind == MessageKind.TEXT.value:
        return True
    metadata = message.message_metadata or {}
    return bool(metadata.get("follow_up")) and metadata.get("delivered") is not False


async def mark_conversation_contact(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID, contact_id: UUID
) -> bool:
    """Make an anonymous outsiders' conversation this contact's, in place.

    Only a conversation already answering people outside the pod: a member's
    conversation never becomes anybody else's. Returns whether it changed.
    """
    conversation = await uow.session.get(ConversationModel, conversation_id)
    metadata = dict(conversation.conversation_metadata or {}) if conversation else {}
    if conversation is None or metadata.get(AUDIENCE_KEY) != OUTSIDERS:
        return False
    metadata.update({AUDIENCE_KEY: CONTACT, CONTACT_KEY: str(contact_id)})
    await uow.session.execute(
        update(ConversationModel)
        .where(ConversationModel.id == conversation_id)
        .values(conversation_metadata=metadata)
    )
    return True


async def move_contact_conversation(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    contact_id: UUID,
    to_user_id: UUID,
) -> bool:
    """Give this contact's conversation to the member now looking after contacts.

    The conversation, not a fresh one: the contact's history is the pod's with
    them, whoever answers for it. Only a conversation marked as this contact's
    moves, so a member's own conversation can never be handed to somebody else
    this way. Returns whether it is now ``to_user_id``'s.
    """
    result = await uow.session.execute(
        update(ConversationModel)
        .where(
            ConversationModel.id == conversation_id,
            ConversationModel.conversation_metadata.contains(
                {AUDIENCE_KEY: CONTACT, CONTACT_KEY: str(contact_id)}
            ),
        )
        .values(user_id=to_user_id)
    )
    return bool(result.rowcount)


async def conversation_handed_to(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID, *, now: datetime
) -> UUID | None:
    """The member answering this conversation by hand right now, if one is."""
    metadata = await uow.session.scalar(
        select(ConversationModel.conversation_metadata).where(
            ConversationModel.id == conversation_id
        )
    )
    return handed_to(metadata, now=now)


async def hold_outside_turn(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    text: str,
    metadata: dict[str, object],
    hand_to: UUID,
    until: datetime,
    reply: str | None,
) -> None:
    """Keep what somebody outside the pod wrote, start no run, and hand it over.

    Their message goes into the conversation as any message would, so the
    member taking it over reads what was asked. ``reply``, when given, is what
    they were told instead of an answer, written as the bot's so the thread
    shows it.
    """
    from app.modules.agent.infrastructure.repositories.conversation_repository import (
        ConversationRepository,
    )

    conversations = ConversationRepository(uow)
    await conversations.append_message(
        conversation_id=conversation_id,
        agent_run_id=None,
        draft=MessageDraft.of_text(text, role=MessageRole.USER, metadata=metadata),
    )
    # The append above holds the row FOR UPDATE, so this read-modify-write
    # cannot lose a concurrent writer's keys.
    model = await uow.session.get(ConversationModel, conversation_id)
    if model is not None:
        stored = dict(model.conversation_metadata or {})
        stored[HANDED_TO_KEY] = {"user_id": str(hand_to), "until": until.isoformat()}
        model.conversation_metadata = stored
    if reply:
        await conversations.append_message(
            conversation_id=conversation_id,
            agent_run_id=None,
            draft=MessageDraft.of_text(reply, metadata={"handed_off": True}),
        )
    await uow.session.flush()


async def append_follow_up(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    message: str,
    sent_by_user_id: UUID,
    delivered: bool | None = None,
) -> None:
    """Write a member's follow-up into a contact's conversation.

    Belongs to no run, like a notification: it is so the agent, and the
    contact reading the thread, see what was sent and can answer it.
    ``delivered`` is False for one the platform refused, so nothing reads it
    as having reached them.
    """
    from app.modules.agent.infrastructure.repositories.conversation_repository import (
        ConversationRepository,
    )

    metadata: dict[str, object] = {
        "follow_up": True,
        "sent_by_user_id": str(sent_by_user_id),
    }
    if delivered is not None:
        metadata["delivered"] = delivered
    await ConversationRepository(uow).append_message(
        conversation_id=conversation_id,
        agent_run_id=None,
        draft=MessageDraft.of_notification(message, metadata=metadata),
    )
