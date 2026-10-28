"""Resolving a pause: recording the decision, then unblocking the run.

A pausing tool call ends its run and leaves a dangling tool call behind. What
resolves it -- a person clicking Approve, a typed reply arriving from Slack, an
unanswered question expiring -- lands here, and all of it has to be idempotent,
because every one of those paths can be retried.

Three things are idempotent for three different reasons, and they are the
substance of this use case and the two it hands on to:

* The **decision** is the pause ending once (``Pause.resolve``), written fenced
  on its PENDING status. That fence is the double-submit lock; a second click
  adopts the first decision rather than recording a new one.
* The **tool return** is guarded by checking for an existing return before
  building one (``ReconcileDecision``). This is the one that matters most:
  building an approved ``request_approval`` return *runs the wrapped tool*, so a
  second build would re-deploy the app or re-run the command.
* The **resume run** is guarded by the conversation lock plus a check that every
  pausing call in the paused run is now resolved (``ResumeRun``), so two
  near-simultaneous resolves start one run between them, not two.

Callers have authorized the resolver against the conversation: the web's own
entry point is ``DecidePause``; a surface matched the person who tapped against
the conversation's owner; an expiry acts for nobody.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.application.pauses.reconcile import ReconcileDecision
from app.modules.agent.domain.approvals import (
    ApprovalResolution,
    ApprovalResolutionStatus,
)
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.errors import (
    ApprovalNotOwnedError,
    UnknownApprovalError,
)
from app.modules.agent.domain.pauses import Pause
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.services.approval_reconciliation import (
    runs_in_worker,
    stage_approval_execution,
)


class Ending(Enum):
    """How the pause ends: a person's decision, or its time running out."""

    DECISION = "decision"
    EXPIRY = "expiry"


class ResolvePause:
    """Turns a person's answer to a pause into a decision and a resumed run."""

    def __init__(
        self, uow: SqlAlchemyUnitOfWork, *, reconcile: ReconcileDecision
    ) -> None:
        self.uow = uow
        self.pauses = Pauses(uow)
        self.reconcile = reconcile

    @classmethod
    def build(cls, uow: SqlAlchemyUnitOfWork) -> ResolvePause:
        return cls(uow, reconcile=ReconcileDecision.build(uow))

    async def resolve(
        self,
        *,
        conversation: Conversation,
        approval_id: str,
        user_id: UUID,
        pod_id: UUID,
        decision: AgentRunApprovalDecision,
        response: dict[str, object] | None = None,
        agent_name: str | None = None,
        ending: Ending = Ending.DECISION,
    ) -> ApprovalResolution:
        """Resume a paused run for an already-authorized + loaded conversation.

        Idempotent and self-healing:
          * no pause for the call -> it is unknown (``UnknownApprovalError``);
          * still PENDING -> end it (fenced on PENDING, so a concurrent
            double-submit loses), then reconcile;
          * already ended -> adopt what was recorded and reconcile (the retry /
            self-heal path) instead of erroring.

        An approved ``request_approval`` runs its wrapped tool, which can take
        minutes, so it never runs here: the decision's transaction writes an
        ``agent.approval.execute`` item and the status comes back ``"queued"``
        (``approval_work`` runs it with the conversation owner's authority).
        Everything else -- answers, denials, Agent Host permissions -- is
        database work and finishes inline.
        """
        # The approvals list's `id` is the message's; accept it for the call.
        approval_id = await self._canonical_approval_id(conversation.id, approval_id)
        pause = await self.pauses.get(
            conversation_id=conversation.id, tool_call_id=approval_id
        )
        if pause is None or pause.kind is PauseKind.WAIT:
            raise UnknownApprovalError()
        won, pause = await self._end(
            pause, conversation, user_id, decision, response, ending
        )
        status = (
            ApprovalResolutionStatus.RESOLVED
            if won
            else ApprovalResolutionStatus.RECONCILED
        )
        if runs_in_worker(pause):
            # In the decision's transaction, so the two commit together: the
            # old post-commit enqueue could die between the commit and Redis
            # and leave a decision nothing would ever carry out. A second
            # decider's item coalesces with the first while it is queued.
            await stage_approval_execution(
                self.uow,
                conversation_id=conversation.id,
                approval_id=approval_id,
                user_id=user_id,
                pod_id=pod_id,
            )
            await self.uow.commit()
            return ApprovalResolution(
                status=ApprovalResolutionStatus.QUEUED, decision=_decided(pause)
            )
        await self.uow.commit()
        await self.reconcile.reconcile(
            conversation=conversation,
            pause=pause,
            user_id=user_id,
            agent_name=agent_name,
        )
        return ApprovalResolution(status=status, decision=_decided(pause))

    async def _end(
        self,
        pause: Pause,
        conversation: Conversation,
        user_id: UUID,
        decision: AgentRunApprovalDecision,
        response: dict[str, object] | None,
        ending: Ending,
    ) -> tuple[bool, Pause]:
        """End a PENDING pause: whether this call did, and the pause as it now
        stands. One that lost the race is read again, for what the winner
        recorded."""
        if not pause.is_open:
            if pause.decision is None and not pause.returned:
                # Ended undecided (a stopped wait, a cancelled call): nothing
                # is left to decide.
                raise UnknownApprovalError()
            return False, pause
        if pause.kind is not PauseKind.QUESTION and user_id != conversation.user_id:
            # An approved call runs with the owner's authority (see
            # `ApprovedExecution`), so only the owner may decide it. Every
            # entry point checks this already; this is the one place all of
            # them pass through, so it is enforced here too.
            raise ApprovalNotOwnedError()
        if ending is Ending.EXPIRY:
            pause.expire(datetime.now(UTC), on_behalf_of=user_id)
        else:
            pause.resolve(decision, response, by=user_id)
        if await self.pauses.save(pause):
            return True, pause
        stored = await self.pauses.get(
            conversation_id=pause.conversation_id, tool_call_id=pause.tool_call_id
        )
        return False, stored or pause

    async def _canonical_approval_id(
        self, conversation_id: UUID, approval_id: str
    ) -> str:
        """``approval_id``, or the call's tool_call_id when it names the call's
        message instead -- what the approvals list hands out as `id`."""
        try:
            message_id = UUID(approval_id)
        except ValueError:
            return approval_id
        found = await self.pauses.tool_call_id_of_message(
            conversation_id=conversation_id, message_id=message_id
        )
        return found or approval_id


def _decided(pause: Pause) -> AgentRunApprovalDecision:
    return pause.decision or AgentRunApprovalDecision.DENY
