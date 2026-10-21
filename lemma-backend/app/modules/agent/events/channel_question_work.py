"""Questions agents put to people, kept in step with the conversations that asked.

* ``agent.pause_questions`` -- subscribed to ``AgentRunFinished`` for a run a
  member is behind. For a member's conversation, one that paused asks its new
  questions as lines waiting on the person; any run's end -- the resume after
  an answer, a stop, a failure -- closes the lines of questions no longer
  waiting, with the decision taken elsewhere or cancelled
  (``services.pause_questions``). And the run's own batch of questions to
  people is sealed: if everyone it asked has already answered, its
  conversation gets its turn back now. FAST.
* ``agent.asked_question_closed`` -- subscribed to channels'
  ``ChannelQuestionClosed`` for a question a conversation put to a member
  (``msg:``): once the asking run has finished and nothing of its batch still
  waits, the asking conversation gets its turn back (``agent.replies_arrived``,
  one per batch). FAST.

A batch settles only after the run that asked has finished. Each
``channel_post`` commits on its own while the run goes on, so "nothing of the
batch waits" is not yet "everyone was asked": a first person answering before
the run asks a second would otherwise settle the batch, and the second
person's answer -- the same batch -- find its news already delivered. The run's
end and each close both check, each after its own commit, so whichever comes
last sees the other.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    pod_namespace,
    registry as default_registry,
)
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.agent.domain.asked_questions import batch_of_run, run_of_batch
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.events.turn_payloads import RepliesArrived
from app.modules.agent.events.work_kinds import (
    AGENT_ASKED_QUESTION_CLOSED,
    AGENT_PAUSE_QUESTIONS,
    AGENT_REPLIES_ARRIVED,
    asked_question_closed_key,
    pause_questions_key,
    replies_key,
)
from app.modules.agent.services.pause_questions import sync_pause_questions
from app.modules.channels.contracts.facts import ChannelQuestionClosed
from app.modules.channels.contracts.questions import (
    Answering,
    QuestionAsker,
    answers_for,
    batch_progress,
)

#: A run in these may still put questions to people.
_STILL_RUNNING = frozenset(
    {AgentRunStatus.RUNNING.value, AgentRunStatus.STOP_REQUESTED.value}
)


class PauseQuestionsPayload(BaseModel):
    """The fields of ``AgentRunFinished`` this needs; the rest are ignored."""

    model_config = ConfigDict(extra="ignore")

    run_id: UUID
    conversation_id: UUID
    pod_id: UUID
    user_id: UUID | None = None
    parent_kind: str | None = None


class QuestionClosedPayload(BaseModel):
    """The fields of ``ChannelQuestionClosed`` this needs; the rest are ignored."""

    model_config = ConfigDict(extra="ignore")

    pod_id: UUID
    message_id: UUID
    ref: str
    batch_id: str | None = None
    asker: QuestionAsker
    asker_conversation_id: UUID | None = None


def concerns_a_person(fact: AgentRunFinished | PauseQuestionsPayload) -> bool:
    """A run of a conversation somebody could have been asked something in:
    a member's, which a person -- not a pod, not a parent agent -- answers."""
    return fact.user_id is not None and fact.parent_kind != "agent"


def may_have_asked_people(fact: AgentRunFinished) -> bool:
    """A run with a member behind it -- a sub-agent's too -- may have put
    questions to people (``channel_post`` with ``wants_answer``)."""
    return fact.user_id is not None


def closes_an_asked_batch(fact: ChannelQuestionClosed) -> bool:
    return (
        fact.batch_id is not None
        and fact.asker is QuestionAsker.MESSAGE
        and fact.asker_conversation_id is not None
    )


@answers_for(QuestionAsker.MESSAGE)
async def answer_message(answering: Answering) -> None:
    """A question one conversation put to a member, answered: taken as given.
    Nothing waits on it but the batch, which its close settles (below)."""
    return


async def after_run(ctx: FastContext, payload: PauseQuestionsPayload) -> None:
    if concerns_a_person(payload):
        await sync_pause_questions(ctx.uow, payload.conversation_id)
    await _wake_if_settled(
        ctx,
        pod_id=payload.pod_id,
        conversation_id=payload.conversation_id,
        batch_id=batch_of_run(payload.run_id),
    )


async def settle_batch(ctx: FastContext, payload: QuestionClosedPayload) -> None:
    conversation_id = payload.asker_conversation_id
    if payload.asker is not QuestionAsker.MESSAGE or conversation_id is None:
        return
    if payload.batch_id is None:
        return
    run_id = run_of_batch(payload.batch_id)
    if run_id is not None and not await _run_finished(ctx, run_id):
        # The asking run may ask somebody else yet; its own end checks again.
        return
    await _wake_if_settled(
        ctx,
        pod_id=payload.pod_id,
        conversation_id=conversation_id,
        batch_id=payload.batch_id,
    )


async def _run_finished(ctx: FastContext, run_id: UUID) -> bool:
    status = await ctx.uow.session.scalar(
        select(AgentRunModel.status).where(AgentRunModel.id == run_id)
    )
    # A run that is gone asks nobody anything more.
    return status is None or status not in _STILL_RUNNING


async def _wake_if_settled(
    ctx: FastContext, *, pod_id: UUID, conversation_id: UUID, batch_id: str
) -> None:
    progress = await batch_progress(ctx.uow, pod_id=pod_id, batch_id=batch_id)
    if not progress.settled:
        return
    await ctx.work.add(
        AGENT_REPLIES_ARRIVED,
        pod_namespace(pod_id),
        RepliesArrived(
            conversation_id=conversation_id, pod_id=pod_id, batch_id=batch_id
        ),
        dedup_key=replies_key(batch_id),
    )


def register_channel_question_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=AGENT_PAUSE_QUESTIONS,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=PauseQuestionsPayload,
    )(after_run)
    registry.handler(
        kind=AGENT_ASKED_QUESTION_CLOSED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=QuestionClosedPayload,
    )(settle_batch)


register_channel_question_work(default_registry)
facts.subscribe(
    AgentRunFinished,
    kind=AGENT_PAUSE_QUESTIONS,
    cls=WorkClass.EVENTS,
    when=may_have_asked_people,
    dedup=lambda fact: pause_questions_key(fact.run_id),
)
facts.subscribe(
    ChannelQuestionClosed,
    kind=AGENT_ASKED_QUESTION_CLOSED,
    cls=WorkClass.EVENTS,
    when=closes_an_asked_batch,
    dedup=lambda fact: asked_question_closed_key(fact.message_id),
)


__all__ = [
    "after_run",
    "answer_message",
    "closes_an_asked_batch",
    "concerns_a_person",
    "may_have_asked_people",
    "register_channel_question_work",
    "settle_batch",
]
