"""Agents answering members' lines in Lemma's channels.

* ``agent.channel_line`` -- subscribed to ``ChannelMessagePosted`` for a line
  some agent answers: writes one ``agent.channel_answer`` per answering agent,
  keyed by (line, agent), in its own transaction. FAST.
* ``agent.channel_answer`` -- starts that agent's turn, as the member, in their
  conversation with it in the channel (``services.channel_answers``). A JOB:
  authorizing the member commits before the conversation is opened. A retry
  finds the turn it already started (the line is the turn's client id). A
  line in another thread of a group than the run its agent is working on
  there is queued behind that run, and answered by the follow-up turn after
  it (``domain/run_places``), as a platform's message said elsewhere is.
* ``agent.channel_reply`` -- subscribed to ``AgentRunFinished`` for a run of
  a person's own conversation: the one way any run's answer is said
  (``services.run_answers``). At the place of the lines it answers: in
  Lemma, in that line's thread -- its answer, or why there is none -- and
  its live readers told it is done either way; on a platform, as the
  agent's line with a delivery there. A contact's run that failed calls for
  a person (``services.contact_run_failures``). FAST; the run is the line's
  client id, so a retry posts nothing new.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.work import (
    FastContext,
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    pod_namespace,
    registry as default_registry,
)
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.agent.events.work_kinds import (
    AGENT_CHANNEL_ANSWER,
    AGENT_CHANNEL_LINE,
    AGENT_CHANNEL_REPLY,
    channel_answer_key,
    channel_line_key,
    channel_reply_key,
)
from app.modules.agent.services.channel_answers import answer_line
from app.modules.agent.services.run_answers import post_run_answer
from app.modules.agent.services.contact_run_failures import after_contact_run
from app.modules.channels.contracts.facts import ChannelMessagePosted


class ChannelLinePayload(BaseModel):
    """The fields of ``ChannelMessagePosted`` this needs; the rest are ignored."""

    model_config = ConfigDict(extra="ignore")

    pod_id: UUID
    message_id: UUID
    answering_agent_ids: tuple[UUID, ...] = ()


class ChannelAnswerWork(BaseModel):
    pod_id: UUID
    message_id: UUID
    agent_id: UUID


class ChannelReplyPayload(BaseModel):
    """The fields of ``AgentRunFinished`` this needs; the rest are ignored."""

    model_config = ConfigDict(extra="ignore")

    run_id: UUID
    conversation_id: UUID
    status: str
    conversation_status: str | None = None


def sets_agents_answering(fact: ChannelMessagePosted) -> bool:
    return bool(fact.answering_agent_ids)


def runs_for_a_member(fact: AgentRunFinished) -> bool:
    """A run of a person's own conversation, however it ended: a member's,
    or a contact's, which the member looking after them keeps."""
    return fact.user_id is not None and fact.parent_kind is None


def ended_with_an_answer(status: str, conversation_status: str | None) -> bool:
    """It ended with an answer, not a failure, a stop or a pause."""
    return status == "COMPLETED" and conversation_status != "WAITING"


async def fan_out(ctx: FastContext, payload: ChannelLinePayload) -> None:
    for agent_id in dict.fromkeys(payload.answering_agent_ids):
        await ctx.work.add(
            AGENT_CHANNEL_ANSWER,
            pod_namespace(payload.pod_id),
            ChannelAnswerWork(
                pod_id=payload.pod_id, message_id=payload.message_id, agent_id=agent_id
            ),
            dedup_key=channel_answer_key(payload.message_id, agent_id),
        )


def make_channel_answer(uow_factory: UnitOfWorkFactory | None = None):
    async def channel_answer(ctx: JobContext, payload: ChannelAnswerWork) -> None:
        ctx.raise_if_stopping()
        await answer_line(
            uow_factory or SessionUnitOfWorkFactory(async_session_maker),
            message_id=payload.message_id,
            agent_id=payload.agent_id,
        )

    return channel_answer


async def post_reply(ctx: FastContext, payload: ChannelReplyPayload) -> None:
    await post_run_answer(
        ctx.uow,
        run_id=payload.run_id,
        conversation_id=payload.conversation_id,
        answered=ended_with_an_answer(payload.status, payload.conversation_status),
        paused=payload.conversation_status == "WAITING",
    )
    await after_contact_run(
        ctx.uow,
        run_id=payload.run_id,
        conversation_id=payload.conversation_id,
        status=payload.status,
    )


def register_channel_answer_work(
    registry: WorkRegistry, *, uow_factory: UnitOfWorkFactory | None = None
) -> None:
    registry.handler(
        kind=AGENT_CHANNEL_LINE,
        cls=WorkClass.INTERACTIVE,
        mode=WorkMode.FAST,
        payload=ChannelLinePayload,
    )(fan_out)
    registry.handler(
        kind=AGENT_CHANNEL_ANSWER,
        cls=WorkClass.INTERACTIVE,
        mode=WorkMode.JOB,
        payload=ChannelAnswerWork,
    )(make_channel_answer(uow_factory))
    register_run_answer_work(registry)


def register_run_answer_work(registry: WorkRegistry) -> None:
    """Only the answer: what a run's end owes, wherever it was asked."""
    registry.handler(
        kind=AGENT_CHANNEL_REPLY,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=ChannelReplyPayload,
    )(post_reply)


register_channel_answer_work(default_registry)
facts.subscribe(
    ChannelMessagePosted,
    kind=AGENT_CHANNEL_LINE,
    cls=WorkClass.INTERACTIVE,
    when=sets_agents_answering,
    dedup=lambda fact: channel_line_key(fact.message_id),
)
facts.subscribe(
    AgentRunFinished,
    kind=AGENT_CHANNEL_REPLY,
    cls=WorkClass.EVENTS,
    when=runs_for_a_member,
    dedup=lambda fact: channel_reply_key(fact.run_id),
)


__all__ = [
    "ChannelAnswerWork",
    "ended_with_an_answer",
    "fan_out",
    "make_channel_answer",
    "post_reply",
    "register_channel_answer_work",
    "register_run_answer_work",
    "runs_for_a_member",
    "sets_agents_answering",
]
