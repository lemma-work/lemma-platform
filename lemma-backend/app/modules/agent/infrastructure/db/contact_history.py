"""What a person outside the pod saw of their conversations, read back.

Their words, the bot's answers and members' follow-ups -- never a tool call,
the model's thinking, another notification, a member's private note, or what
a note's run said back. The same rule answers a contact's export, a web
visitor's history, and what a merge copies from a conversation that lost its
seat into the one that kept it (``application/channels/fold``).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, func, literal, select, tuple_
from sqlalchemy.orm import aliased

from app.modules.agent.domain.contact_history import (
    CLIENT_NONCE_KEY,
    FOLLOW_UP_LINE_KEY,
    MAX_EXPORTED_CONVERSATIONS,
    MAX_EXPORTED_MESSAGES,
    ConversationCursor,
    ConversationsPage,
    ExportedConversation,
    ExportedMessage,
    VisibleMessage,
)
from app.modules.agent.domain.conversations import MOVED_MESSAGE_KEYS
from app.modules.agent.domain.message_state import MessageState
from app.modules.agent.domain.value_objects import (
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.db.base import UnitOfWorkRepository
from app.modules.agent.infrastructure.models.conversation import (
    AgentRunModel,
    ConversationModel,
    MessageModel,
)
from app.modules.channels.contracts.backgrounds import current_words

#: The message kinds a person outside the pod may have been shown.
_SEEN_KINDS = (MessageKind.TEXT.value, MessageKind.NOTIFICATION.value)


def _seen_by_them() -> Select[tuple[MessageModel, bool | None]]:
    """Messages a person outside the pod could have seen, with whether their
    run was a private note's."""
    return (
        select(MessageModel, AgentRunModel.private_note.label("run_private"))
        .outerjoin(AgentRunModel, AgentRunModel.id == MessageModel.agent_run_id)
        .where(
            MessageModel.role.in_(("user", "assistant")),
            MessageModel.kind.in_(_SEEN_KINDS),
            MessageModel.text.is_not(None),
            MessageModel.tool_name.is_(None),
        )
    )


def _follow_up_line(message: MessageModel) -> UUID | None:
    metadata = message.message_metadata or {}
    if not metadata.get("follow_up"):
        return None
    try:
        return UUID(str(metadata.get(FOLLOW_UP_LINE_KEY)))
    except ValueError:
        return None


def _was_said_to_them(message: MessageModel) -> bool:
    """Text, or a member's follow-up that reached them -- never another notification."""
    if message.kind == MessageKind.TEXT.value:
        return True
    metadata = message.message_metadata or {}
    return bool(metadata.get("follow_up")) and metadata.get("delivered") is not False


def _was_seen(message: MessageModel, run_private: bool | None) -> bool:
    return _was_said_to_them(message) and not message.private_note and not run_private


def _client_nonce(message: MessageModel) -> str | None:
    """The page's name for a message the visitor sent; nobody else's has one."""
    if message.role != "user":
        return None
    nonce = (message.message_metadata or {}).get(CLIENT_NONCE_KEY)
    return nonce if isinstance(nonce, str) else None


class ContactHistory(UnitOfWorkRepository):
    """Reads of what contacts saw, by contact or by conversation."""

    async def export_page(
        self,
        *,
        pod_id: UUID,
        contact_id: UUID,
        after: ConversationCursor | None,
        limit: int,
    ) -> ConversationsPage:
        """A page of this contact's conversations, with what they saw of each.

        Two reads a page, whatever its size: the conversations, then every
        message of all of them.
        """
        page_size = max(1, min(limit, MAX_EXPORTED_CONVERSATIONS))
        query = select(ConversationModel).where(
            ConversationModel.pod_id == pod_id,
            ConversationModel.contact_id == contact_id,
        )
        if after is not None:
            query = query.where(
                tuple_(ConversationModel.created_at, ConversationModel.id)
                > tuple_(
                    literal(after.created_at, ConversationModel.created_at.type),
                    literal(after.conversation_id, ConversationModel.id.type),
                )
            )
        conversations = list(
            await self.session.scalars(
                query.order_by(
                    ConversationModel.created_at, ConversationModel.id
                ).limit(page_size + 1)
            )
        )
        more = len(conversations) > page_size
        conversations = conversations[:page_size]
        said = await self._messages_of(
            [conversation.id for conversation in conversations]
        )
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

    async def visible(
        self, conversation_id: UUID, *, after: int, limit: int
    ) -> tuple[VisibleMessage, ...]:
        """What the person outside the pod saw of a conversation, after a point."""
        rows = await self.session.execute(
            _seen_by_them()
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.sequence > after,
            )
            .order_by(MessageModel.sequence)
            .limit(limit)
        )
        seen = [m for m, run_private in rows if _was_seen(m, run_private)]
        said = await self._said(seen)
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

    async def copies_of(self, conversation_id: UUID) -> list[MessageDraft]:
        """What the person saw of a conversation, as the messages a merge
        copies into the one that kept the seat, marked as folded from it."""
        rows = await self.session.execute(
            _seen_by_them()
            .where(MessageModel.conversation_id == conversation_id)
            .order_by(MessageModel.sequence)
            .limit(MAX_EXPORTED_MESSAGES)
        )
        return [
            MessageDraft(
                role=MessageRole(message.role),
                kind=MessageKind(message.kind),
                text=message.text,
                metadata={
                    key: value
                    for key, value in (message.message_metadata or {}).items()
                    if key not in MOVED_MESSAGE_KEYS
                },
                state=MessageState(answer=message.answer, folded_from=conversation_id),
            )
            for message, run_private in rows
            if _was_seen(message, run_private) and message.folded_from is None
        ]

    async def _messages_of(
        self, conversation_ids: list[UUID]
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
        seen_model = aliased(MessageModel, numbered)
        rows = await self.session.execute(
            select(seen_model, numbered.c.run_private)
            .where(numbered.c.place <= MAX_EXPORTED_MESSAGES)
            .order_by(numbered.c.conversation_id, numbered.c.sequence)
        )
        # A folded copy was exported with the conversation it came from.
        seen = [
            row
            for row, run_private in rows
            if _was_seen(row, run_private) and row.folded_from is None
        ]
        said = await self._said(seen)
        grouped: dict[UUID, list[ExportedMessage]] = {}
        for row in seen:
            if row.id in said:
                grouped.setdefault(row.conversation_id, []).append(
                    ExportedMessage(
                        role=row.role,
                        text=said[row.id],
                        created_at=row.created_at,
                        sequence=row.sequence,
                    )
                )
        return {key: tuple(value) for key, value in grouped.items()}

    async def _said(self, messages: list[MessageModel]) -> dict[UUID, str]:
        """What each message says, by its id: a member's follow-up says what its
        line says now, and one whose line is deleted or gone is not there."""
        lines = {
            message.id: line_id
            for message in messages
            if (line_id := _follow_up_line(message)) is not None
        }
        words = await current_words(self.uow, list(lines.values())) if lines else {}
        said: dict[UUID, str] = {}
        for message in messages:
            if message.id not in lines:
                said[message.id] = message.text or ""
            elif (text := words.get(lines[message.id])) is not None:
                said[message.id] = text
        return said
