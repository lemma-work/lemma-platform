"""The work that ends an agent's ``wait_for``.

* ``agent.wait.fire`` (FAST, events) -- written with the wait row, due at its
  ``scheduled_at``. A TIME wait wakes: wait COMPLETED, tool return, resume run
  (``stage_agent_turn``), and the item's deletion in one transaction. For a
  SUBAGENT wait it is the deadline, and finds the wait already ended when the
  child finished first.
* ``agent.wait.poll`` (JOB, automation) -- a PROCESS wait's look at its
  sandbox. The probe runs outside any transaction; the completion re-reads the
  wait under its lock and either wakes it or re-arms the same key for the next
  look. A wait cancelled while the probe ran is not re-armed.
* ``agent.wait.resolve_child`` (FAST, events) -- subscribed to
  ``AgentRunFinished``: a finished child resolves its parent's SUBAGENT wait in
  its own item, never inside the child's finalize (which holds the child's
  rows and would then want the parent's).

These replace the schedule poller's ``claim_due_waits``, the
``workflow-schedule-events`` hop and the five-minute ``reconcile_agent_waits``
sweep; an item that keeps failing goes dead, and its hook ends the wait FAILED.

FAST handlers live here, so this module imports no network client (the
sandbox probe is reached through ``AgentWaitService``).
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import (
    DeadContext,
    Exponential,
    FastContext,
    JobContext,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.agent.events.work_kinds import (
    AGENT_WAIT_FIRE,
    AGENT_WAIT_POLL,
    AGENT_WAIT_RESOLVE_CHILD,
    child_wait_key,
)
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.runtime_owned_uow import RuntimeOwnedUnitOfWork
from app.modules.agent.services.wait_timers import WaitTimer
from app.modules.agent.services.wait_wake_service import (
    AgentWaitService,
    probe_process_target,
)

logger = get_logger(__name__)

WAIT_RETRY = RetryPolicy(
    max_attempts=5,
    backoff=Exponential(base=timedelta(seconds=5), cap=timedelta(minutes=5)),
)


class ChildRunFinished(BaseModel):
    """The part of ``AgentRunFinished`` a parent's wait needs."""

    run_id: UUID
    conversation_id: UUID


def _service(ctx: FastContext, uow: SqlAlchemyUnitOfWork) -> AgentWaitService:
    return AgentWaitService(uow, work=ctx.work)


async def fire_wait(ctx: FastContext, payload: WaitTimer) -> None:
    uow = RuntimeOwnedUnitOfWork(ctx.uow)
    await _service(ctx, uow).fire(payload.wait_id)


async def poll_wait(ctx: JobContext, payload: WaitTimer) -> None:
    async with ctx.checkpoint() as uow:
        # Unlocked; `poll` re-reads the wait under its lock before acting.
        target = await AgentWaitService(uow).process_target(payload.wait_id)
    if target is None:
        return
    outcome = await probe_process_target(target)
    async with ctx.complete_work() as uow:
        held = RuntimeOwnedUnitOfWork(uow)
        await AgentWaitService(held, work=ctx.writer(uow)).poll(
            payload.wait_id, outcome
        )


async def resolve_child_wait(ctx: FastContext, payload: ChildRunFinished) -> None:
    uow = RuntimeOwnedUnitOfWork(ctx.uow)
    await _service(ctx, uow).resolve_child(
        child_run_id=payload.run_id, child_conversation_id=payload.conversation_id
    )


async def on_wait_dead(ctx: DeadContext) -> None:
    """A wake that keeps failing ends the wait FAILED instead of retrying forever.

    The conversation is left WAITING rather than resumed: no tool return could
    be built, so there is nothing truthful to hand the model. The error log is
    the operator's signal.
    """
    try:
        payload = WaitTimer.model_validate(ctx.item.payload)
    except ValueError:
        return
    waits = AgentConversationWaitRepository(ctx.uow)
    claimed = await waits.claim(payload.wait_id)
    if claimed is None or not claimed.abandon(f"wake failed: {ctx.reason}"):
        return
    await waits.update(claimed)
    logger.error(
        "agent.wait.abandoned",
        conversation_id=str(claimed.conversation_id),
        wait_id=str(claimed.id),
        reason=ctx.reason,
    )


def register_wait_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=AGENT_WAIT_FIRE,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=WaitTimer,
        retry=WAIT_RETRY,
        on_dead=on_wait_dead,
    )(fire_wait)
    registry.handler(
        kind=AGENT_WAIT_POLL,
        cls=WorkClass.AUTOMATION,
        mode=WorkMode.JOB,
        payload=WaitTimer,
        retry=WAIT_RETRY,
        on_dead=on_wait_dead,
    )(poll_wait)
    registry.handler(
        kind=AGENT_WAIT_RESOLVE_CHILD,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=ChildRunFinished,
        retry=WAIT_RETRY,
    )(resolve_child_wait)


register_wait_work(default_registry)
facts.subscribe(
    AgentRunFinished,
    kind=AGENT_WAIT_RESOLVE_CHILD,
    cls=WorkClass.EVENTS,
    # Every child run, waited on or not: whether a parent waits is the parent
    # conversation's state, which a predicate on the fact cannot read.
    when=lambda fact: fact.parent_kind == "agent",
    dedup=lambda fact: child_wait_key(fact.run_id),
)

__all__ = [
    "ChildRunFinished",
    "WAIT_RETRY",
    "fire_wait",
    "on_wait_dead",
    "poll_wait",
    "register_wait_work",
    "resolve_child_wait",
]
