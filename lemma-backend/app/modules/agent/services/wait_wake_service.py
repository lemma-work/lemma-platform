"""Resolving a suspended conversation's wait, and resuming the agent.

Every wait owns one work item, keyed ``wait:<id>`` and written with the wait
row: ``agent.wait.fire`` (a TIME wait's time, a SUBAGENT wait's deadline) or
``agent.wait.poll`` (a PROCESS wait's next look). The entry points differ in
who already knows the answer:

* :meth:`AgentWaitService.fire` -- a timer came due. Database only, so it runs
  inside the item's own transaction.
* :meth:`AgentWaitService.poll` -- a PROCESS wait's poll. The caller probes
  the sandbox first (:func:`probe_process_target`), outside any transaction --
  a five-second probe must not hold a row lock -- and hands the outcome in.
* :meth:`AgentWaitService.resolve_child` -- the child a SUBAGENT wait is on
  finished.
* :meth:`AgentWaitService.wake` -- the caller knows the reason (every message
  the conversation sent was answered).

All of them lock the wait row and act only on an ACTIVE wait, so a second
resolution -- a stop racing the timer, a child finishing at its deadline -- is
a no-op rather than a second run. Every ending drops the wait's queued item in
the same transaction; a running one finds the wait no longer ACTIVE and stops.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from app.core.authorization.context import Context
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter
from app.core.log.log import get_logger
from app.modules.agent.domain.pausing_tools import WAIT_TOOL_NAME
from app.modules.agent.domain.value_objects import RunTrigger
from app.modules.agent.domain.wait import (
    AgentConversationWaitEntity,
    AgentWaitStatus,
    AgentWaitType,
    AgentWaitWakeReason,
)
from app.modules.agent.domain.wait_decision import WakeNow, deadline_of, decide_wait
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.wait_timers import WaitTimer, arm_wait, disarm_wait
from app.modules.agent.services.wait_targets import (
    TargetOutcome,
    read_subagent_in,
    read_target,
)
from app.modules.agent.tools.waiting.models import (
    build_wait_result,
    elapsed_seconds,
    next_poll_delay,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ProcessTarget:
    """What a polled wait looks at, read so the probe can run unlocked."""

    wait_type: AgentWaitType
    target_ref: str | None
    user_id: UUID | None
    pod_id: UUID
    conversation_id: UUID


async def probe_process_target(target: ProcessTarget) -> TargetOutcome:
    """Look at the process: sandbox I/O, so never inside a transaction."""
    if target.user_id is None:
        return TargetOutcome(reason=None)
    return await read_target(
        wait_type=target.wait_type,
        target_ref=target.target_ref,
        user_id=target.user_id,
        pod_id=target.pod_id,
        conversation_id=target.conversation_id,
    )


class AgentWaitService:
    def __init__(self, uow: SqlAlchemyUnitOfWork, *, work: WorkWriter | None = None):
        self.uow = uow
        self.waits = AgentConversationWaitRepository(uow)
        self.conversations = Conversations(uow)
        self.work = work or WorkWriter.of(uow)

    async def fire(self, wait_id: UUID) -> bool:
        """A wait's timer came due. Database only; idempotent.

        Returns True only when the conversation was resumed.
        """
        claimed = await self._claim(wait_id)
        if claimed is None:
            return False
        if claimed.wait_type is AgentWaitType.PROCESS:
            # A polled wait's look reads the sandbox, which is not this
            # transaction's to do; hand it to its own item.
            await arm_wait(self.work, claimed, None)
            await self.uow.commit()
            return False
        outcome = TargetOutcome(reason=None)
        if claimed.wait_type is AgentWaitType.SUBAGENT:
            outcome = await self._read_child(claimed)
        return await self._decide(claimed, outcome)

    async def process_target(self, wait_id: UUID) -> ProcessTarget | None:
        """What a PROCESS wait watches; None for a wait that has ended.

        Read without a lock, so the caller can probe it with no transaction
        open (:func:`probe_process_target`); :meth:`poll` re-reads the wait
        under its lock before acting on the answer.
        """
        wait = await self.waits.get(wait_id)
        if wait is None or wait.status is not AgentWaitStatus.ACTIVE:
            return None
        return ProcessTarget(
            wait_type=wait.wait_type,
            target_ref=wait.wait_spec.target_ref,
            user_id=await self._user_of(wait),
            pod_id=wait.pod_id,
            conversation_id=wait.conversation_id,
        )

    async def poll(self, wait_id: UUID, outcome: TargetOutcome) -> bool:
        """Act on a PROCESS wait's look: wake, or re-arm for the next one.

        Re-reads the wait under its row lock first. A wait cancelled while the
        probe ran is not re-armed -- that is what makes a stop stick against a
        poll that was already running when it landed.
        """
        claimed = await self._claim(wait_id)
        if claimed is None:
            return False
        return await self._decide(claimed, outcome)

    async def resolve_child(
        self, *, child_run_id: UUID, child_conversation_id: UUID
    ) -> bool:
        """The child a parent's SUBAGENT wait is on has finished a run.

        Keyed on the *parent conversation*: a conversation has at most one
        ACTIVE wait (the partial unique index), and the parent may be waiting
        on a *different* child -- resolving that one would wake it to a result
        it never asked for.
        """
        child = await self.conversations.get_conversation(
            child_conversation_id, include_runs=False
        )
        if child is None or child.parent_id is None:
            return False
        wait = await self.waits.find_active_for_conversation(child.parent_id)
        if wait is None or wait.wait_type is not AgentWaitType.SUBAGENT:
            return False
        if str(wait.wait_spec.target_ref) != str(child_run_id):
            return False
        claimed = await self._claim(wait.id)
        if claimed is None:
            return False
        return await self._decide(claimed, await self._read_child(claimed))

    async def wake(
        self,
        *,
        wait: AgentConversationWaitEntity,
        reason: AgentWaitWakeReason = AgentWaitWakeReason.TIMER,
    ) -> bool:
        """Resolve the wait for a reason the caller already knows. Idempotent.

        Returns False when another resolution already ended it.
        """
        claimed = await self._claim(wait.id)
        if claimed is None:
            logger.debug("agent.wait.already_claimed", wait_id=str(wait.id))
            return False
        return await self._wake_claimed(claimed, reason)

    # -- internals ---------------------------------------------------------------

    async def _claim(self, wait_id: UUID) -> AgentConversationWaitEntity | None:
        """Lock the conversation, then the wait; the wait only if still ACTIVE.

        In that order everywhere a wait ends -- a stop, a new message, every
        message append -- so a timer racing a person never deadlocks with them.
        """
        wait = await self.waits.get(wait_id)
        if wait is None or wait.status is not AgentWaitStatus.ACTIVE:
            return None
        await self.conversations.lock_conversation(wait.conversation_id)
        return await self.waits.claim(wait_id)

    async def _user_of(self, wait: AgentConversationWaitEntity) -> UUID | None:
        if wait.wait_spec.user_id is not None:
            return wait.wait_spec.user_id
        conversation = await self.conversations.get_conversation(
            wait.conversation_id, include_runs=False
        )
        return conversation.user_id if conversation else None

    async def _read_child(self, wait: AgentConversationWaitEntity) -> TargetOutcome:
        user_id = await self._user_of(wait)
        target_ref = wait.wait_spec.target_ref
        if user_id is None or not target_ref:
            return TargetOutcome(reason=None)
        return await read_subagent_in(
            self.uow,
            target_ref,
            user_id=user_id,
            parent_conversation_id=wait.conversation_id,
        )

    async def _decide(
        self, claimed: AgentConversationWaitEntity, outcome: TargetOutcome
    ) -> bool:
        now = datetime.now(timezone.utc)
        spec = claimed.wait_spec
        # The first failed read starts the clock; any read that answers stops
        # it. Only an unbroken stretch of silence counts against the grace.
        unreadable_since: datetime | None = None
        if outcome.unreadable:
            unreadable_since = spec.unreadable_since or now
            logger.warning(
                "agent.wait.target_unreadable.degraded",
                wait_id=str(claimed.id),
                conversation_id=str(claimed.conversation_id),
                unreadable_seconds=int((now - unreadable_since).total_seconds()),
            )
        decision = decide_wait(
            claimed,
            target_reason=outcome.reason,
            now=now,
            poll_delay_seconds=next_poll_delay(spec.poll_attempt),
            unreadable_since=unreadable_since,
        )
        if isinstance(decision, WakeNow):
            return await self._wake_claimed(
                claimed, decision.reason, exit_code=outcome.exit_code
            )
        if claimed.wait_type is AgentWaitType.SUBAGENT:
            # Pushed, not polled: nothing to look at before the deadline, which
            # is when its timer is due. Fired early, the deadline item is
            # re-armed (and a queued one is simply kept).
            await arm_wait(self.work, claimed, deadline_of(claimed, fallback=now))
            await self.uow.commit()
            return False
        claimed.rearm(decision.next_at, unreadable_since=unreadable_since)
        await self.waits.update(claimed)
        await arm_wait(self.work, claimed, decision.next_at)
        await self.uow.commit()
        return False

    async def _wake_claimed(
        self,
        claimed: AgentConversationWaitEntity,
        reason: AgentWaitWakeReason,
        *,
        exit_code: int | None = None,
    ) -> bool:
        conversation = await self.conversations.get_conversation(
            claimed.conversation_id, include_runs=False
        )
        await disarm_wait(self.work, claimed)
        if conversation is None:
            claimed.cancel()
            await self.waits.update(claimed)
            await self.uow.commit()
            return False

        claimed.complete(reason)
        await self.waits.update(claimed)

        ctx = await self._context_for(conversation.user_id, claimed.pod_id)
        token = set_current_context(ctx)
        try:
            # Lazy, as the service it replaced was: the waiting tool imports
            # this module, and the pause use cases load the tool models.
            from app.modules.agent.application.pauses.resume import ResumeRun

            await ResumeRun.build(self.uow).resume(
                conversation=conversation,
                paused_run_id=claimed.agent_run_id,
                tool_call_id=claimed.tool_call_id,
                user_id=conversation.user_id,
                trigger=RunTrigger.WAKE,
                tool_return=(
                    WAIT_TOOL_NAME,
                    self._tool_result(claimed, reason, exit_code),
                ),
            )
        finally:
            reset_current_context(token)

        logger.debug(
            "agent.wait.woke",
            conversation_id=str(claimed.conversation_id),
            wait_type=claimed.wait_type.value,
            woke_because=reason.value,
        )
        return True

    def _tool_result(
        self,
        wait: AgentConversationWaitEntity,
        reason: AgentWaitWakeReason,
        exit_code: int | None,
    ) -> dict[str, object]:
        spec = wait.wait_spec
        return build_wait_result(
            wait_type=wait.wait_type,
            reason=reason,
            waited_seconds=elapsed_seconds(spec.started_at),
            note_to_self=spec.note_to_self,
            exit_code=exit_code,
        )

    async def _context_for(self, user_id: UUID, pod_id: UUID) -> Context:
        return await create_authorization_data_service(self.uow).build_user_context(
            user_id=user_id,
            pod_id=pod_id,
        )


__all__ = ["AgentWaitService", "ProcessTarget", "WaitTimer", "probe_process_target"]
