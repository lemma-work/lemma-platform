"""A contact's conversations, for the module that forgets and exports contacts.

A contact's conversation names them in its ``contact_id`` column
(``domain/outsiders``), and that column is how these find them. Deleting cascades to its runs, messages,
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
    and_,
    delete,
    func,
    literal,
    select,
    tuple_,
    update,
)
from sqlalchemy.orm import aliased

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.held_turns import held_messages
from app.modules.agent.domain.private_notes import is_private_note, run_is_private
from app.modules.agent.domain.value_objects import MessageDraft, MessageKind
from app.modules.agent.infrastructure.models.conversation import (
    AgentRunModel,
    ConversationModel,
    MessageModel,
)
from app.modules.channels.contracts.backgrounds import current_words

#: Where a member's follow-up names the line it marks.
FOLLOW_UP_LINE_KEY = "line_id"

#: A message copied from a conversation that lost its seat when its contact
#: was merged into another: the conversation it came from.
FOLDED_FROM_KEY = "folded_from"

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
    #: Written by a member of the pod, not the agent: a person answered.
    from_person: bool = False


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
    return (
        ConversationModel.pod_id == pod_id,
        ConversationModel.contact_id == contact_id,
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
    # A folded copy was exported with the conversation it came from.
    seen = [
        row
        for row, run_metadata in rows
        if _was_seen(row, run_metadata)
        and FOLDED_FROM_KEY not in (row.message_metadata or {})
    ]
    said = await _said(uow, seen)
    grouped: dict[UUID, list[ExportedMessage]] = {}
    for row in seen:
        if row.id in said:
            grouped.setdefault(row.conversation_id, []).append(
                _exported(row, said[row.id])
            )
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
    seen = [
        message for message, run_metadata in rows if _was_seen(message, run_metadata)
    ]
    said = await _said(uow, seen)
    return tuple(
        VisibleMessage(
            role=message.role,
            text=said[message.id],
            created_at=message.created_at,
            sequence=message.sequence,
            client_nonce=_client_nonce(message),
            from_person=_follow_up_line(message) is not None,
        )
        for message in seen
        if message.id in said
    )


async def _said(
    uow: SqlAlchemyUnitOfWork, messages: list[MessageModel]
) -> dict[UUID, str]:
    """What each message says, by its id: a member's follow-up says what its
    line says now, and one whose line is deleted or gone is not there."""
    lines = {
        message.id: line_id
        for message in messages
        if (line_id := _follow_up_line(message)) is not None
    }
    words = await current_words(uow, list(lines.values())) if lines else {}
    said: dict[UUID, str] = {}
    for message in messages:
        if message.id not in lines:
            said[message.id] = message.text or ""
        elif (text := words.get(lines[message.id])) is not None:
            said[message.id] = text
    return said


def _follow_up_line(message: MessageModel) -> UUID | None:
    metadata = message.message_metadata or {}
    if not metadata.get("follow_up"):
        return None
    try:
        return UUID(str(metadata.get(FOLLOW_UP_LINE_KEY)))
    except ValueError:
        return None


def _exported(message: MessageModel, text: str) -> ExportedMessage:
    return ExportedMessage(
        role=message.role,
        text=text,
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


async def name_contact_on_conversation(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID, contact_id: UUID
) -> bool:
    """Make a conversation that answers nobody yet this contact's, in place.

    For a web visitor's chat opened before anything named them. A member's
    conversation never becomes a contact's: only a conversation with no
    contact, opened by a surface for somebody outside the pod, is changed --
    which the caller vouches for by holding that conversation's visitor
    session. Returns whether it changed.
    """
    result = await uow.session.execute(
        update(ConversationModel)
        .where(
            ConversationModel.id == conversation_id,
            ConversationModel.contact_id.is_(None),
        )
        .values(contact_id=contact_id)
    )
    return bool(result.rowcount)


async def repoint_merged_contact(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, merged_id: UUID, survivor_id: UUID
) -> int:
    """Conversations with a contact since merged are the survivor's now.

    A person has one live conversation per agent in a channel: where the
    survivor already has one with the same agent in the same channel, it
    keeps the seat, and the merged contact's loses it -- it leaves the
    channel, as a surface expects of a conversation a merge unseated, and the
    person's next message continues the one that kept it.
    """
    conversation, theirs = ConversationModel, aliased(ConversationModel)
    unseated = await uow.session.execute(
        select(conversation.id, theirs.id)
        .join(
            theirs,
            and_(
                _live_in_a_channel(theirs),
                theirs.channel_id == conversation.channel_id,
                _same_agent(theirs, conversation),
                theirs.contact_id == survivor_id,
            ),
        )
        .where(*_theirs(pod_id, merged_id), _live_in_a_channel(conversation))
    )
    pairs = [(row[0], row[1]) for row in unseated]
    if pairs:
        from app.modules.agent.contracts.folded_history import fold_history

        await uow.session.execute(
            update(conversation)
            .where(conversation.id.in_([orphan for orphan, _ in pairs]))
            .values(channel_id=None)
            .execution_options(synchronize_session=False)
        )
        for orphan, seated in pairs:
            await fold_history(uow, orphan, into=seated)
    result = await uow.session.execute(
        update(ConversationModel)
        .where(*_theirs(pod_id, merged_id))
        .values(contact_id=survivor_id)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


async def move_channel_conversations(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    from_channel_id: UUID,
    to_channel_id: UUID,
) -> int:
    """A channel folded into another: the conversations working in it work in
    that one now, before the emptied channel is dropped and unplaces them.

    Where the person already has a live conversation with the same agent
    there, that one keeps the seat and this one stays behind, unseated when
    the channel goes (``repoint_merged_contact``) -- what was said in it is
    folded into the one that kept the seat (``folded_history``). Returns how
    many moved.
    """
    conversation, theirs = ConversationModel, aliased(ConversationModel)
    here = (
        conversation.pod_id == pod_id,
        conversation.channel_id == from_channel_id,
    )
    same_person_there = and_(
        _live_in_a_channel(theirs),
        theirs.channel_id == to_channel_id,
        _same_agent(theirs, conversation),
        func.coalesce(theirs.contact_id, theirs.user_id)
        == func.coalesce(conversation.contact_id, conversation.user_id),
    )
    seated_there = select(theirs.id).where(same_person_there).exists()
    staying = await uow.session.execute(
        select(conversation.id, theirs.id)
        .join(theirs, same_person_there)
        .where(*here, _live_in_a_channel(conversation))
    )
    from app.modules.agent.contracts.folded_history import fold_history

    for orphan, seated in [(row[0], row[1]) for row in staying]:
        await fold_history(uow, orphan, into=seated)
    result = await uow.session.execute(
        update(conversation)
        .where(*here, ~(_live_in_a_channel(conversation) & seated_there))
        .values(channel_id=to_channel_id)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


def _live_in_a_channel(conversation: type[ConversationModel]) -> ColumnElement[bool]:
    """What ``uq_agent_conversations_channel_person`` holds to one per person."""
    return (
        conversation.channel_id.is_not(None)
        & conversation.parent_id.is_(None)
        & conversation.is_archived.is_(False)
    )


def _same_agent(
    one: type[ConversationModel], other: type[ConversationModel]
) -> ColumnElement[bool]:
    return func.coalesce(one.agent_id, one.pod_id) == func.coalesce(
        other.agent_id, other.pod_id
    )


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
            ConversationModel.contact_id == contact_id,
        )
        .values(user_id=to_user_id)
    )
    return bool(result.rowcount)


async def hold_outside_turn(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    text: str,
    metadata: dict[str, object],
    reply: str | None,
) -> None:
    """Keep what somebody outside the pod wrote, and start no run.

    Their message goes into the conversation as any message would, so the
    member answering it reads what was asked. ``reply``, when given, is what
    they were told instead of an answer, written as the bot's so the thread
    shows it. Both are marked held (``held_turns``): in no run, the mark is
    what carries them to the agent once it answers them again.
    """
    from app.modules.agent.infrastructure.repositories.conversation_repository import (
        ConversationRepository,
    )

    conversations = ConversationRepository(uow)
    for draft in held_messages(text, metadata, reply=reply):
        await conversations.append_message(
            conversation_id=conversation_id, agent_run_id=None, draft=draft
        )
    await uow.session.flush()


async def append_follow_up(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    line_id: UUID,
    sent_by_user_id: UUID,
    delivered: bool | None = None,
) -> None:
    """Mark where a member's line to a contact falls in their conversation.

    Belongs to no run, like a notification: it is so the agent, and the
    contact reading the thread, see what was sent and can answer it. It keeps
    which line, never the line's words: those are read from the channel
    whenever the conversation is (``_said``), so an edit reads as it says
    now and a deleted line is gone. ``delivered`` is False for one the
    platform refused, so nothing reads it as having reached them. One mark
    per line: a retried send brings its ``delivered`` up to date rather than
    adding a second.
    """
    from app.modules.agent.infrastructure.repositories.conversation_repository import (
        ConversationRepository,
    )

    earlier = await notification_keyed(
        uow, conversation_id, key=FOLLOW_UP_LINE_KEY, value=str(line_id)
    )
    if earlier is not None:
        if delivered is not None:
            earlier.message_metadata = {
                **(earlier.message_metadata or {}),
                "delivered": delivered,
            }
            await uow.session.flush()
        return
    metadata: dict[str, object] = {
        "follow_up": True,
        "sent_by_user_id": str(sent_by_user_id),
        FOLLOW_UP_LINE_KEY: str(line_id),
    }
    if delivered is not None:
        metadata["delivered"] = delivered
    await ConversationRepository(uow).append_message(
        conversation_id=conversation_id,
        agent_run_id=None,
        draft=MessageDraft.of_notification("", metadata=metadata),
    )


async def notification_keyed(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID, *, key: str, value: str
) -> MessageModel | None:
    """The conversation's notification whose metadata has ``key`` = ``value``,
    read under the conversation's row lock -- the lock every append takes --
    so two sends of one line cannot both find none and both append."""
    await uow.session.execute(
        select(ConversationModel.id)
        .where(ConversationModel.id == conversation_id)
        .with_for_update()
    )
    return await uow.session.scalar(
        select(MessageModel)
        .where(
            MessageModel.conversation_id == conversation_id,
            MessageModel.kind == MessageKind.NOTIFICATION.value,
            MessageModel.message_metadata[key].astext == value,
        )
        .order_by(MessageModel.sequence.desc())
        .limit(1)
    )
