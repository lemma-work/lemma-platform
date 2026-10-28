"""A call's conversations, and what its canvas showed.

A call is a coordinator's conversation (``ConversationOrigin.CALL``, its
``call`` column naming itself) and its sub-agents'. Its canvas is every
display tool call any of them made that succeeded.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select

from app.modules.agent.domain.calls import CANVAS_TOOL_NAMES, CallMark, CallRole
from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import MessageKind
from app.modules.agent.infrastructure.db.base import UnitOfWorkRepository
from app.modules.agent.infrastructure.models import ConversationModel, MessageModel

#: Sub-agents one call may draw from. A coordinator spawns one per distinct
#: ask; a call that has spawned more than this has stopped being a call.
MAX_CALL_CONVERSATIONS = 200


class Calls(UnitOfWorkRepository):
    """Reads of a call's conversations and its canvas."""

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

    async def conversations(self, call_id: UUID) -> list[UUID]:
        """The call's own conversation, then each sub-agent's, oldest first."""
        rows = await self.session.execute(
            select(ConversationModel.id)
            .where(
                or_(
                    ConversationModel.id == call_id,
                    ConversationModel.parent_id == call_id,
                )
            )
            .order_by(ConversationModel.created_at)
            .limit(MAX_CALL_CONVERSATIONS)
        )
        return list(rows.scalars())

    async def canvas_messages(self, conversation_ids: list[UUID]) -> list[Message]:
        """The display tool calls and returns in these conversations, in order."""
        rows = await self.session.scalars(
            select(MessageModel)
            .where(
                MessageModel.conversation_id.in_(conversation_ids),
                MessageModel.tool_name.in_(CANVAS_TOOL_NAMES),
                MessageModel.kind.in_(
                    [MessageKind.TOOL_CALL.value, MessageKind.TOOL_RETURN.value]
                ),
            )
            .order_by(MessageModel.created_at, MessageModel.sequence)
        )
        return [row.to_entity() for row in rows]
