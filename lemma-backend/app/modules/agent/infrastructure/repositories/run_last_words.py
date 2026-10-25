"""A run's last words, and the write that makes them its answer.

The answer a run ends on is what its callers read as the result: a workflow
step, a schedule's ledger, a parent agent, a channel's reply, the CLI. A run
can reach the end having said its answer without it being recorded as one. An
Agent Host announces a call its adapter opened but never settled only when the
turn ends, after the agent's closing words, and words followed by a call are
sealed as narration -- so the answer is on the record as a step on the way.

Plain functions over a session, with two callers (``run_finalizer`` and
``run_message_writer``) and no repository of their own.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.domain.entities import Message as MessageEntity
from app.modules.agent.domain.value_objects import (
    AnswerKind,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.models import MessageModel


async def last_text(
    session: AsyncSession, *, agent_run_id: UUID
) -> MessageEntity | None:
    """The last assistant text the run wrote, if it said anything."""
    found = await session.execute(
        select(MessageModel)
        .where(
            MessageModel.agent_run_id == agent_run_id,
            MessageModel.role == MessageRole.ASSISTANT.value,
            MessageModel.kind == MessageKind.TEXT.value,
        )
        .order_by(MessageModel.sequence.desc())
        .limit(1)
    )
    model = found.scalar_one_or_none()
    return model.to_entity() if model is not None else None


async def tool_result_after(
    session: AsyncSession, *, agent_run_id: UUID, sequence: int
) -> bool:
    """Whether a tool returned a result in this run after ``sequence``.

    A return the run's ending made up for a call that never ran is not one
    (``missing_tool_return_events``): no result of the tool's own.
    """
    return bool(
        await session.scalar(
            select(
                exists().where(
                    MessageModel.agent_run_id == agent_run_id,
                    MessageModel.sequence > sequence,
                    MessageModel.kind == MessageKind.TOOL_RETURN.value,
                    ~MessageModel.synthetic_tool_return,
                )
            )
        )
    )


async def mark_final_answer(
    session: AsyncSession, *, message_id: UUID
) -> MessageEntity | None:
    """Make a message its run's answer: final, and no longer a step on the way.

    ``LAST_WORDS`` says why a message sent as narration reads as the answer:
    the run ended without one and these were its last words. Queryable, so how
    often an adapter leaves an answer to be found this way can be counted.
    """
    row = (
        await session.execute(
            update(MessageModel)
            .where(MessageModel.id == message_id)
            .values(answer=AnswerKind.LAST_WORDS)
            .returning(MessageModel)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    return row.to_entity() if row is not None else None


__all__ = [
    "last_text",
    "mark_final_answer",
    "tool_result_after",
]
