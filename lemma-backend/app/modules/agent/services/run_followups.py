"""Everything a finished run is owed, written in the transaction that finished it.

A run ending is the moment several parties stop waiting: the person who typed
while it was busy, a parent agent, a pod that asked, a workflow step, a
schedule's ledger, the surface the conversation answers on. Each of those used
to hear about it through a separate hop -- an outbox row, a stream, a consumer
group, an inbox -- and each hop could lose it, which is what the repair sweeps
were for. Here they are work items committed with the run's terminal status, so
the outcome and the follow-ups exist together or not at all.

Two kinds of follow-up, both in the same transaction:

* **Other modules** hear through the ``AgentRunFinished`` fact: one item per
  matching subscriber (workflow resume, schedule outcome, the surface reply,
  the parent's SUBAGENT wait), each deciding with a pure predicate.
* **This module's own** follow-ups are written directly, and only when the
  conversation can owe them: queued messages, a sub-agent's result to a parent
  that did not wait, a pod-to-pod answer, and the memory pass of a run a person
  was waiting on. Each runs in its own item, in its own transaction -- a
  child's finalize never takes its parent's row lock, which is the lock-order
  hazard of doing the delivery inline.

Only the transaction that actually moved the run (``updated``) writes any of
this, so a second finalize of the same run writes nothing.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter, facts, pod_namespace
from app.core.log.log import get_logger
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.run_waiters import run_waiters
from app.modules.agent.domain.value_objects import (
    AgentRunFinishResult,
    AgentRunStatus,
)
from app.modules.agent.events.turn_payloads import ConversationWork, MemoryPassWork
from app.modules.agent.events.work_kinds import (
    AGENT_FOLLOWUP,
    AGENT_MEMORY,
    AGENT_SUBAGENT_RESULT,
    POD_ASK_DELIVER,
    followup_key,
    memory_pass_key,
    memory_pod_key,
    pod_ask_key,
    subagent_result_key,
)
from app.modules.agent.infrastructure.db.conversations import Conversations

logger = get_logger(__name__)


def _registered() -> None:
    import app.modules.agent.events.run_followup_work  # noqa: F401


def run_finished_fact(
    conversation: Conversation,
    *,
    run_id: UUID,
    finish: AgentRunFinishResult,
    started_at: datetime | None = None,
) -> AgentRunFinished:
    """The fact a finished run announces: its kind, and who waits on it."""
    waiters = run_waiters(conversation)
    status = finish.status
    if status not in (
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.STOPPED,
    ):
        raise ValueError(f"run {run_id} is not finished: {status.value}")
    return AgentRunFinished(
        run_id=run_id,
        conversation_id=conversation.id,
        pod_id=conversation.pod_id,
        organization_id=conversation.organization_id,
        agent_id=conversation.agent_id,
        user_id=conversation.user_id,
        status=status.value,
        conversation_status=finish.conversation_status.value,
        parent_kind=waiters.parent_kind,
        parent_ref=waiters.parent_ref,
        origin=finish.kind.value,
        started_at=started_at,
    )


async def stage_run_followups(
    uow: SqlAlchemyUnitOfWork,
    *,
    run_id: UUID,
    conversation_id: UUID,
    finish: AgentRunFinishResult,
    started_at: datetime | None = None,
    keep_memory_for: UUID | None = None,
) -> AgentRunFinished | None:
    """Write every follow-up of a run that ``finish`` just ended, in ``uow``.

    ``keep_memory_for`` is the person the run worked for, when it was told its
    memory is kept after it ends: a completed one is owed that pass, which acts
    for the same person. Not the conversation's owner -- in a shared
    conversation the run answers whoever spoke, and their ``/me`` is theirs.

    Returns the fact, or ``None`` when there is nothing to write: the finish
    moved nothing (a second finalize), or the conversation is gone.
    """
    if not finish.updated:
        return None
    conversation = await Conversations(uow).get_conversation(conversation_id)
    if conversation is None:
        return None
    _registered()
    fact = run_finished_fact(
        conversation, run_id=run_id, finish=finish, started_at=started_at
    )
    work = WorkWriter.of(uow)
    subscribers = await facts.emit(uow, fact, writer=work)
    waiters = run_waiters(conversation)
    ns = pod_namespace(conversation.pod_id)
    payload = ConversationWork(conversation_id=conversation.id, run_id=run_id)
    # Every finish: what was said while it ran, or a pause answered before it
    # finished ending. The handler's first read is the cheap no-op.
    await work.add(AGENT_FOLLOWUP, ns, payload, dedup_key=followup_key(run_id))
    if waiters.settles_subagents:
        await work.add(
            AGENT_SUBAGENT_RESULT, ns, payload, dedup_key=subagent_result_key(run_id)
        )
    if waiters.settles_pod_asks:
        await work.add(POD_ASK_DELIVER, ns, payload, dedup_key=pod_ask_key(run_id))
    # Completed only: a failed or stopped turn's words are said again in the
    # turn that answers them, and a pass over a half-finished one would keep
    # what the person was still in the middle of saying.
    if keep_memory_for is not None and finish.status is AgentRunStatus.COMPLETED:
        await work.add(
            AGENT_MEMORY,
            ns,
            MemoryPassWork(
                conversation_id=conversation.id, run_id=run_id, user_id=keep_memory_for
            ),
            dedup_key=memory_pass_key(run_id),
            concurrency_key=memory_pod_key(conversation.pod_id),
        )
    logger.debug(
        "agent.run.followups_staged.observed",
        agent_run_id=str(run_id),
        subscribers=subscribers,
    )
    return fact


__all__ = ["run_finished_fact", "stage_run_followups"]
