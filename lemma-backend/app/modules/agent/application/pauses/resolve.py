"""Resolving a pause: recording the decision, then unblocking the run.

A pausing tool call ends its run and leaves a dangling tool call behind. What
resolves it -- a person clicking Approve, a typed reply arriving from Slack, an
unanswered question expiring -- lands here, and all of it has to be idempotent,
because every one of those paths can be retried.

Three things are idempotent for three different reasons, and they are the
substance of this use case and the two it hands on to:

* The **decision** is guarded by a unique ``(conversation_id, approval_id)`` row.
  That constraint is the double-submit lock; a second click adopts the first
  decision rather than recording a new one.
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
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent.infrastructure.db.approvals import ApprovalDecisions
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.services.approval_reconciliation import (
    runs_in_worker,
    stage_approval_execution,
)
from app.modules.agent.services.pause_resume import paused_call
from app.modules.agent.domain.pausing_tools import PausingKind


class ResolvePause:
    """Turns a person's answer to a pause into a decision and a resumed run."""

    def __init__(
        self, uow: SqlAlchemyUnitOfWork, *, reconcile: ReconcileDecision
    ) -> None:
        self.uow = uow
        self.pausing_calls = Pauses(uow)
        self.approval_decisions = ApprovalDecisions(uow)
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
    ) -> ApprovalResolution:
        """Resume a paused run for an already-authorized + loaded conversation.

        Idempotent and self-healing. Classifies the approval three ways:
          * neither a paused call nor a recorded decision exists -> it is unknown
            (``UnknownApprovalError`` -> 404);
          * no decision yet -> record it (the unique (conversation, approval) row
            is the double-submit lock), then reconcile;
          * a decision already exists -> adopt it and reconcile (the retry /
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
        decision_row = await self.approval_decisions.get_approval_decision(
            conversation_id=conversation.id,
            approval_id=approval_id,
        )
        paused = await paused_call(
            self.pausing_calls,
            conversation_id=conversation.id,
            approval_id=approval_id,
        )
        if decision_row is None and paused is None:
            # Nothing to resolve and nothing to heal — the approval never existed
            # or its call was never persisted.
            raise UnknownApprovalError()

        if paused is None:
            # Unreachable in practice: the pausing tool call message is never
            # deleted, so a recorded decision always has its call. Guard anyway —
            # we cannot faithfully rebuild a resume without the original call.
            raise UnknownApprovalError()
        kind = paused.kind
        tool_args = paused.tool_args
        paused_run_id = paused.agent_run_id

        if decision_row is None:
            if kind != PausingKind.ASK_USER and user_id != conversation.user_id:
                # An approved call runs with the owner's authority (see
                # `ApprovedExecution`), so only the owner may decide it. Every
                # entry point checks this already; this is the one place all
                # of them pass through, so it is enforced here too.
                raise ApprovalNotOwnedError()
            # Fresh resolve: record the decision. The unique (conversation,
            # approval) row locks out a concurrent double-submit before any
            # side-effecting tool runs.
            decision_tool_name = (
                PausingKind.ASK_USER.value
                if kind == PausingKind.ASK_USER
                else str(
                    tool_args.get("tool_name") or PausingKind.REQUEST_APPROVAL.value
                )
            )
            recorded = await self.approval_decisions.record_approval_decision(
                conversation_id=conversation.id,
                approval_id=approval_id,
                agent_run_id=paused_run_id,
                tool_name=decision_tool_name,
                decision=decision,
                response=response or {},
                resolved_by_user_id=user_id,
            )
            if recorded:
                effective_decision, effective_response, status = (
                    decision,
                    response or {},
                    ApprovalResolutionStatus.RESOLVED,
                )
            else:
                # A concurrent resolve won the race; adopt its stored decision and
                # reconcile (idempotent) rather than raising "already resolved".
                stored = await self.approval_decisions.get_approval_decision(
                    conversation_id=conversation.id,
                    approval_id=approval_id,
                )
                effective_decision, effective_response = (
                    stored if stored is not None else (decision, response or {})
                )
                status = ApprovalResolutionStatus.RECONCILED
        else:
            # Decision already recorded (retry / self-heal). Do NOT re-record or
            # re-run the tool; adopt the stored decision and finish whatever the
            # prior attempt left undone.
            effective_decision, effective_response = decision_row
            status = ApprovalResolutionStatus.RECONCILED

        existing_return = await self.pausing_calls.get_tool_return(
            conversation_id=conversation.id,
            tool_call_id=approval_id,
        )
        if runs_in_worker(
            kind=kind,
            tool_args=tool_args,
            decision=effective_decision,
            has_tool_return=existing_return is not None,
        ):
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
                status=ApprovalResolutionStatus.QUEUED, decision=effective_decision
            )
        await self.uow.commit()

        await self.reconcile.reconcile(
            conversation=conversation,
            approval_id=approval_id,
            paused_run_id=paused_run_id,
            kind=kind,
            tool_args=tool_args,
            decision=effective_decision,
            response=effective_response,
            user_id=user_id,
            agent_name=agent_name,
        )
        return ApprovalResolution(status=status, decision=effective_decision)

    async def _canonical_approval_id(
        self, conversation_id: UUID, approval_id: str
    ) -> str:
        """``approval_id``, or the call's tool_call_id when it names the call's
        message instead -- what the approvals list hands out as `id`."""
        try:
            message_id = UUID(approval_id)
        except ValueError:
            return approval_id
        found = await self.pausing_calls.tool_call_id_of_message(
            conversation_id=conversation_id, message_id=message_id
        )
        return found or approval_id
