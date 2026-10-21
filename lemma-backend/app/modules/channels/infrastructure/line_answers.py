"""Which agents answer which lines (``channel_line_answers``).

Written as an agent's turn on a line starts and settled as its run ends, so
the run that finishes knows the lines it was answering by their rows, never by
guessing from what its conversation heard last.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import ColumnElement, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.infrastructure.answer_models import (
    AnswerState,
    ChannelLineAnswerModel,
)
from app.modules.channels.domain.entities import ChannelMessage, SenderKind
from app.modules.channels.infrastructure.message_models import ChannelMessageModel
from app.modules.channels.infrastructure.rows import message_entity

#: The most lines one run is found answering. A burst of lines a person sent
#: while the agent worked joins the one run; past this many it is no burst.
MAX_LINES_A_RUN_ANSWERS = 50


@dataclass(frozen=True, slots=True)
class LineAnswer:
    """One agent answering one line."""

    message_id: UUID
    channel_id: UUID
    agent_id: UUID
    conversation_id: UUID
    agent_run_id: UUID | None
    state: AnswerState
    answer_message_id: UUID | None


def _entity(row: ChannelLineAnswerModel) -> LineAnswer:
    return LineAnswer(
        message_id=row.message_id,
        channel_id=row.channel_id,
        agent_id=row.agent_id,
        conversation_id=row.conversation_id,
        agent_run_id=row.agent_run_id,
        state=AnswerState(row.state),
        answer_message_id=row.answer_message_id,
    )


class LineAnswers:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start(
        self,
        *,
        message_id: UUID,
        channel_id: UUID,
        agent_id: UUID,
        conversation_id: UUID,
    ) -> None:
        """The agent is answering the line in this conversation. A retried
        start keeps the first: an agent answers a line once."""
        await self.session.execute(
            insert(ChannelLineAnswerModel)
            .values(
                message_id=message_id,
                channel_id=channel_id,
                agent_id=agent_id,
                conversation_id=conversation_id,
                state=AnswerState.ANSWERING.value,
            )
            .on_conflict_do_nothing(index_elements=["message_id", "agent_id"])
        )

    async def ran_by(self, *, message_id: UUID, agent_id: UUID, run_id: UUID) -> None:
        """The run answering it -- the one its turn started, or joined."""
        await self.session.execute(
            update(ChannelLineAnswerModel)
            .where(
                ChannelLineAnswerModel.message_id == message_id,
                ChannelLineAnswerModel.agent_id == agent_id,
                ChannelLineAnswerModel.agent_run_id.is_(None),
            )
            .values(agent_run_id=run_id, updated_at=func.now())
        )

    async def of_lines(
        self, conversation_id: UUID, line_ids: Sequence[UUID]
    ) -> list[LineAnswer]:
        """This conversation's answers to these lines, oldest line first."""
        if not line_ids:
            return []
        return await self._ordered(
            ChannelLineAnswerModel.conversation_id == conversation_id,
            ChannelLineAnswerModel.message_id.in_(list(line_ids)),
        )

    async def of_run(self, run_id: UUID) -> list[LineAnswer]:
        """The lines this run answered, oldest first."""
        return await self._ordered(ChannelLineAnswerModel.agent_run_id == run_id)

    async def still_answering(self, conversation_id: UUID) -> list[LineAnswer]:
        """The lines a conversation is still answering, oldest first: what a
        run that carries on a paused one answers."""
        return await self._ordered(
            ChannelLineAnswerModel.conversation_id == conversation_id,
            ChannelLineAnswerModel.state == AnswerState.ANSWERING.value,
        )

    async def last_heard(self, conversation_id: UUID) -> ChannelMessage | None:
        """The latest line a person said to a conversation, wherever they said
        it: heard on a platform in it (the line names the conversation), or
        written in Lemma and taken up by it (a row here). One conversation is
        a person's across every place of its channel, so either may be the
        latest -- and its answer belongs where that one was said."""
        message = ChannelMessageModel
        taken_up = select(ChannelLineAnswerModel.message_id).where(
            ChannelLineAnswerModel.conversation_id == conversation_id
        )
        row = await self.session.scalar(
            select(message)
            .where(
                or_(
                    message.conversation_id == conversation_id, message.id.in_(taken_up)
                ),
                message.sender_kind != SenderKind.AGENT.value,
            )
            .order_by(message.seq.desc())
            .limit(1)
        )
        return message_entity(row) if row is not None else None

    async def latest_of(self, conversation_id: UUID) -> LineAnswer | None:
        """The line a conversation took up last."""
        row = await self.session.scalar(
            select(ChannelLineAnswerModel)
            .where(ChannelLineAnswerModel.conversation_id == conversation_id)
            .order_by(ChannelLineAnswerModel.created_at.desc())
            .limit(1)
        )
        return _entity(row) if row is not None else None

    async def settle(
        self,
        answers: Sequence[LineAnswer],
        *,
        run_id: UUID,
        state: AnswerState,
        answer_message_id: UUID | None = None,
    ) -> None:
        if not answers:
            return
        await self.session.execute(
            update(ChannelLineAnswerModel)
            .where(_keyed(answers))
            .values(
                agent_run_id=run_id,
                state=state.value,
                answer_message_id=answer_message_id,
                updated_at=func.now(),
            )
            .execution_options(synchronize_session=False)
        )

    async def _ordered(self, *where: ColumnElement[bool]) -> list[LineAnswer]:
        answer, line = ChannelLineAnswerModel, ChannelMessageModel
        rows = await self.session.scalars(
            select(answer)
            .join(line, line.id == answer.message_id)
            .where(*where)
            .order_by(line.seq)
            .limit(MAX_LINES_A_RUN_ANSWERS)
        )
        return [_entity(row) for row in rows]


def _keyed(answers: Sequence[LineAnswer]) -> ColumnElement[bool]:
    return tuple_(
        ChannelLineAnswerModel.message_id, ChannelLineAnswerModel.agent_id
    ).in_([(answer.message_id, answer.agent_id) for answer in answers])


__all__ = ["LineAnswer", "LineAnswers", "MAX_LINES_A_RUN_ANSWERS"]
