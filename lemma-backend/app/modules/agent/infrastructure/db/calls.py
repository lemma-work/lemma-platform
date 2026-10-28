"""A call's conversations, what its canvas showed, and what was said on it.

A call is held in a conversation -- a new one (``ConversationOrigin.CALL``)
or the chat it was started from -- whose ``call`` column names it the
coordinator, and in every sub-agent's conversation that carries its mark. Its
canvas is every display tool call any of them made, since the conversation
became a call, that succeeded.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, or_, select, update
from sqlalchemy.orm import aliased

from app.modules.agent.domain.calls import (
    CANVAS_TOOL_NAMES,
    RELAY_TOOL_NAME,
    CallMark,
    CallRole,
)
from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import AgentRunStatus, MessageKind
from app.modules.agent.infrastructure.db.base import UnitOfWorkRepository
from app.modules.agent.infrastructure.models import (
    AgentRunModel,
    ConversationModel,
    MessageModel,
)

#: Sub-agents one call may draw from. A coordinator spawns one per distinct
#: ask; a call that has spawned more than this has stopped being a call.
MAX_CALL_CONVERSATIONS = 200

#: Earlier connections a resumed call reads back, newest kept. The voice is
#: given only the tail of them anyway.
MAX_RESUMED_TRANSCRIPTS = 10

#: Relays a resumed call reads back, at most.
MAX_MISSED_RELAYS = 20

#: The message metadata key a connection's transcript is recorded under.
CALL_TRANSCRIPT_KEY = "call_transcript"


class Calls(UnitOfWorkRepository):
    """Reads of a call's conversations, canvas and transcript."""

    async def owned(
        self, *, conversation_id: UUID, user_id: UUID, pod_id: UUID
    ) -> bool:
        """Whether this is a call's own conversation, in this pod, this person's."""
        row = (
            await self.session.execute(
                select(
                    ConversationModel.user_id,
                    ConversationModel.pod_id,
                    ConversationModel.call,
                ).where(ConversationModel.id == conversation_id)
            )
        ).one_or_none()
        if row is None or row.user_id != user_id or row.pod_id != pod_id:
            return False
        mark = CallMark.from_json(row.call)
        return mark is not None and mark.role is CallRole.COORDINATOR

    async def inherited_by_child(self, conversation_id: UUID) -> CallMark | None:
        """The mark a sub-agent spawned from this conversation inherits.

        Only while the call is live, so a sub-agent spawned from a live call
        draws on its canvas and one spawned in a chat whose call has ended is an
        ordinary sub-agent. The coordinator's own mark decides: a worker's copy
        is never cleared when the call ends, so a worker spawning after hang-up
        would otherwise pull its child into a call that is over.
        """
        mark = CallMark.from_json(await self._stored_mark(conversation_id))
        if mark is None:
            return None
        if mark.call_id != conversation_id:
            coordinator = CallMark.from_json(await self._stored_mark(mark.call_id))
            live = coordinator is not None and coordinator.live
        else:
            live = mark.live
        return mark.for_worker() if live else None

    async def _stored_mark(self, conversation_id: UUID) -> object:
        return await self.session.scalar(
            select(ConversationModel.call).where(
                ConversationModel.id == conversation_id
            )
        )

    async def conversations(self, call_id: UUID) -> list[UUID]:
        """The call's own conversation and every sub-agent's at any depth,
        oldest first.

        By the mark each one carries rather than by parent: a sub-agent's own
        sub-agents are in the call too, and were missed after a reconnect when
        only direct children were read. The newest are kept when there are more.
        """
        pod_id = await self._pod_of(call_id)
        rows = await self.session.execute(
            select(ConversationModel.id)
            .where(
                ConversationModel.pod_id == pod_id,
                or_(
                    ConversationModel.id == call_id,
                    ConversationModel.call["call_id"].astext == str(call_id),
                ),
            )
            .order_by(ConversationModel.created_at.desc())
            .limit(MAX_CALL_CONVERSATIONS)
        )
        return list(reversed(rows.scalars().all()))

    async def _pod_of(self, call_id: UUID) -> UUID | None:
        return await self.session.scalar(
            select(ConversationModel.pod_id).where(ConversationModel.id == call_id)
        )

    async def working(self, conversation_ids: list[UUID]) -> bool:
        """Whether any of these conversations has a run going."""
        return bool(
            await self.session.scalar(
                select(
                    exists().where(
                        AgentRunModel.conversation_id.in_(conversation_ids),
                        AgentRunModel.status.in_(
                            [
                                AgentRunStatus.RUNNING.value,
                                AgentRunStatus.STOP_REQUESTED.value,
                            ]
                        ),
                    )
                )
            )
        )

    async def retire_if_empty(self, call_id: UUID) -> bool:
        """Archive the call's conversation when it has no message and no
        sub-agent at all."""
        child = aliased(ConversationModel)
        result = await self.session.execute(
            update(ConversationModel)
            .where(
                ConversationModel.id == call_id,
                ~exists().where(MessageModel.conversation_id == call_id),
                ~exists().where(child.parent_id == call_id),
            )
            .values(is_archived=True)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def transcripts(self, call_id: UUID) -> list[str]:
        """What was said on the call's earlier connections, oldest first."""
        rows = await self.session.execute(
            select(MessageModel.text)
            .where(
                MessageModel.conversation_id == call_id,
                MessageModel.kind == MessageKind.NOTIFICATION.value,
                MessageModel.message_metadata.has_key(CALL_TRANSCRIPT_KEY),
            )
            .order_by(MessageModel.created_at.desc(), MessageModel.sequence.desc())
            .limit(MAX_RESUMED_TRANSCRIPTS)
        )
        return [text for text in reversed(rows.scalars().all()) if text]

    async def relays_since_last_transcript(
        self, call_id: UUID
    ) -> list[tuple[str, str]]:
        """``(text, urgency)`` of each relay sent since the call's last
        transcript was written, oldest first; none when there is no transcript."""
        last = await self.session.scalar(
            select(MessageModel.created_at)
            .where(
                MessageModel.conversation_id == call_id,
                MessageModel.kind == MessageKind.NOTIFICATION.value,
                MessageModel.message_metadata.has_key(CALL_TRANSCRIPT_KEY),
            )
            .order_by(MessageModel.created_at.desc())
            .limit(1)
        )
        if last is None:
            return []
        rows = await self.session.execute(
            select(MessageModel.tool_args)
            .where(
                MessageModel.conversation_id == call_id,
                MessageModel.kind == MessageKind.TOOL_CALL.value,
                MessageModel.tool_name == RELAY_TOOL_NAME,
                MessageModel.created_at > last,
            )
            .order_by(MessageModel.created_at)
            .limit(MAX_MISSED_RELAYS)
        )
        relays: list[tuple[str, str]] = []
        for args in rows.scalars():
            if isinstance(args, dict) and isinstance(args.get("text"), str):
                relays.append((args["text"], str(args.get("urgency") or "when_idle")))
        return relays

    async def canvas_messages(
        self, conversation_ids: list[UUID], *, since: datetime | None = None
    ) -> list[Message]:
        """The display tool calls and returns in these conversations, in order,
        from ``since`` on when given."""
        rows = await self.session.scalars(
            select(MessageModel)
            .where(
                MessageModel.conversation_id.in_(conversation_ids),
                MessageModel.tool_name.in_(CANVAS_TOOL_NAMES),
                MessageModel.kind.in_(
                    [MessageKind.TOOL_CALL.value, MessageKind.TOOL_RETURN.value]
                ),
                *([MessageModel.created_at >= since] if since is not None else []),
            )
            .order_by(MessageModel.created_at, MessageModel.sequence)
        )
        return [row.to_entity() for row in rows]
