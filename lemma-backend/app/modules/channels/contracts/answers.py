"""An agent answering a line: which run answers which lines, and with what.

A turn answering a line records it as it starts (``start_answering``): the
agent, the conversation it answers in, and the run the turn started or
joined. When a run ends, the lines it answers are the ones whose words it
read -- the message that started it, and those steered into it -- so a
member who wrote L1 and then L2 while the agent worked has the run's answer
under L1, where they asked, never under whichever line the conversation heard
last. Several agents may answer one line, each once.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.infrastructure.answer_models import AnswerState
from app.modules.channels.infrastructure.line_answers import LineAnswer, LineAnswers

__all__ = [
    "AnswerState",
    "LineAnswer",
    "answers_of_run",
    "line_answered_in",
    "lines_of_run",
    "settle_answers",
    "start_answering",
]


async def start_answering(
    uow: SqlAlchemyUnitOfWork,
    *,
    message_id: UUID,
    channel_id: UUID,
    agent_id: UUID,
    conversation_id: UUID,
    run_id: UUID | None = None,
) -> None:
    """``agent_id`` answers the line in this conversation -- by ``run_id``,
    once the turn knows its run. Safe to say twice."""
    answers = LineAnswers(uow.session)
    await answers.start(
        message_id=message_id,
        channel_id=channel_id,
        agent_id=agent_id,
        conversation_id=conversation_id,
    )
    if run_id is not None:
        await answers.ran_by(message_id=message_id, agent_id=agent_id, run_id=run_id)


async def answers_of_run(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    line_ids: list[UUID],
    carries_on: bool,
) -> list[LineAnswer]:
    """The conversation's answers to the lines a run read, oldest line first.

    ``carries_on``: no message started the run -- it resumes a paused one --
    so it also answers what the conversation is still answering.
    """
    answers = LineAnswers(uow.session)
    found = await answers.of_lines(conversation_id, line_ids)
    if not carries_on:
        return found
    pending = await answers.still_answering(conversation_id)
    known = {answer.message_id for answer in pending}
    return pending + [answer for answer in found if answer.message_id not in known]


async def settle_answers(
    uow: SqlAlchemyUnitOfWork,
    answers: list[LineAnswer],
    *,
    run_id: UUID,
    answered_with: UUID | None,
) -> None:
    """``run_id`` ended: with the line ``answered_with``, or without an answer."""
    await LineAnswers(uow.session).settle(
        answers,
        run_id=run_id,
        state=AnswerState.ANSWERED if answered_with else AnswerState.ENDED,
        answer_message_id=answered_with,
    )


async def line_answered_in(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID, *, run_id: UUID | None = None
) -> UUID | None:
    """The line ``run_id`` answers in this conversation -- else the line the
    conversation took up last."""
    answers = LineAnswers(uow.session)
    if run_id is not None:
        of_run = await answers.of_run(run_id)
        if of_run:
            return of_run[0].message_id
    latest = await answers.latest_of(conversation_id)
    return latest.message_id if latest is not None else None


async def lines_of_run(uow: SqlAlchemyUnitOfWork, run_id: UUID) -> list[UUID]:
    """The lines ``run_id`` answers, oldest first."""
    return [
        answer.message_id for answer in await LineAnswers(uow.session).of_run(run_id)
    ]
