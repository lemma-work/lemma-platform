"""The reads a memory pass makes of the turn it keeps.

A collaborator over a session, with one caller
(``application/background/memory_pass``) and a projection nothing else uses.

Every body is cut in SQL. A run's tool results can be whole files and web
pages, and the pass reads a few hundred characters of each, so materialising
them as entities first would move megabytes to throw nearly all of it away.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Text, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.modules.agent.domain.memory_pass_turn import (
    REPLY_BEFORE_MAX_CHARS,
    STEP_MAX_CHARS,
    TURN_MESSAGES_MAX,
    WORDS_MAX_CHARS,
    TurnMessage,
)
from app.modules.agent.domain.value_objects import MessageKind, MessageRole
from app.modules.agent.infrastructure.models import MessageModel

_READ_KINDS = (
    MessageKind.TEXT.value,
    MessageKind.TOOL_CALL.value,
    MessageKind.TOOL_RETURN.value,
)


def _body() -> ColumnElement[str | None]:
    """A message's text, arguments or result, cut one past its cap.

    One past, so the renderer can tell a body was cut without reading the
    rest of it.
    """
    return case(
        (
            MessageModel.kind == MessageKind.TEXT.value,
            func.left(MessageModel.text, WORDS_MAX_CHARS + 1),
        ),
        (
            MessageModel.kind == MessageKind.TOOL_CALL.value,
            func.left(cast(MessageModel.tool_args, Text), STEP_MAX_CHARS + 1),
        ),
        else_=func.left(cast(MessageModel.tool_result, Text), STEP_MAX_CHARS + 1),
    )


class FinishedTurnQueries:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_turn_messages(self, agent_run_id: UUID) -> list[TurnMessage]:
        """The run's text and tool messages in order (``ix_agent_message_run_sequence``)."""
        rows = await self.session.execute(
            select(
                MessageModel.role, MessageModel.kind, _body(), MessageModel.tool_name
            )
            .where(
                MessageModel.agent_run_id == agent_run_id,
                MessageModel.kind.in_(_READ_KINDS),
            )
            .order_by(MessageModel.sequence)
            .limit(TURN_MESSAGES_MAX)
        )
        return [_message(*row) for row in rows.all()]

    async def list_tool_exchange(
        self, conversation_id: UUID, tool_call_id: str
    ) -> list[TurnMessage]:
        """One tool call and its return, whichever runs they are stored on.

        A run resumed by a person's answer holds neither: the question is on
        the run that paused, and the answer was stored beside it.
        """
        rows = await self.session.execute(
            select(
                MessageModel.role, MessageModel.kind, _body(), MessageModel.tool_name
            )
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.tool_call_id == tool_call_id,
                MessageModel.kind.in_(_READ_KINDS),
            )
            .order_by(MessageModel.sequence)
            .limit(2)
        )
        return [_message(*row) for row in rows.all()]

    async def reply_before_run(
        self, conversation_id: UUID, agent_run_id: UUID
    ) -> str | None:
        """The agent's last text before ``agent_run_id`` began, cut one past its cap."""
        first = (
            select(func.min(MessageModel.sequence))
            .where(MessageModel.agent_run_id == agent_run_id)
            .scalar_subquery()
        )
        return await self.session.scalar(
            select(func.left(MessageModel.text, REPLY_BEFORE_MAX_CHARS + 1))
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.role == MessageRole.ASSISTANT.value,
                MessageModel.kind == MessageKind.TEXT.value,
                MessageModel.text.is_not(None),
                MessageModel.sequence < first,
            )
            .order_by(MessageModel.sequence.desc())
            .limit(1)
        )


def _message(
    role: str, kind: str, body: str | None, tool_name: str | None
) -> TurnMessage:
    return TurnMessage(
        role=MessageRole(role), kind=MessageKind(kind), body=body, tool_name=tool_name
    )


__all__ = ["FinishedTurnQueries"]
