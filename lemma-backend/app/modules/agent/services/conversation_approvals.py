"""Resolving a pause: recording the decision, then unblocking the run.

A pausing tool call ends its run and leaves a dangling tool call behind. What
resolves it -- a person clicking Approve, a typed reply arriving from Slack, a
newer message superseding an unanswered question -- all lands here, and all of
it has to be idempotent, because every one of those paths can be retried.

Three things are idempotent for three different reasons, and they are the
substance of this module:

* The **decision** is guarded by a unique ``(conversation_id, approval_id)`` row.
  That constraint is the double-submit lock; a second click adopts the first
  decision rather than recording a new one.
* The **tool return** is guarded by checking for an existing return before
  building one. This is the one that matters most: building an approved
  ``request_approval`` return *runs the wrapped tool*, so a second build would
  re-deploy the app or re-run the command.
* The **resume run** is guarded by the conversation lock plus a check that every
  pausing call in the paused run is now resolved, so two near-simultaneous
  resolves start one run between them, not two.

A resolve that dies halfway -- decision written, return not appended, run not
started -- self-heals the next time the user retries, because each half checks
for its own effect before doing it again.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host_permissions import (
    agent_host_permission_request,
)
from app.modules.agent.domain.approvals import ApprovalResolution
from app.modules.agent.domain.entities import Conversation, Message
from app.modules.agent.domain.errors import UnknownApprovalError
from app.modules.agent.domain.pausing_tools import PAUSING_TOOL_NAMES
from app.modules.agent.domain.ports import ConversationRepository
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    JsonObject,
    MessageDraft,
    MessageKind,
)
from app.modules.agent.services.conversation_resume_return import (
    ResumeToolReturnBuilder,
)
from app.modules.agent.services.approval_reconciliation import (
    queue_approval_reconciliation,
    should_defer_approved_tool,
)
from app.modules.agent.services.pause_resume import PauseResume

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PausedCall:
    """The pausing tool call a resolve is about.

    A record rather than the `dict[str, object]` this used to be: the lookup
    already proves the run id is present and the kind is one of the pausing
    tools, and a dict threw both facts away one line later -- so every field had
    to be re-derived, `str()`-ed or re-checked at each use.
    """

    agent_run_id: UUID
    kind: str
    tool_args: JsonObject


class ApprovalCoordinator:
    """Turns a resolved pause into a persisted decision and a resumed run."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        conversation_repository: ConversationRepository,
        resume_returns: ResumeToolReturnBuilder,
        pauses: PauseResume,
    ) -> None:
        self.uow = uow
        self.conversation_repository = conversation_repository
        self.resume_returns = resume_returns
        self.pauses = pauses

    async def resolve_user_approval_internal(
        self,
        *,
        conversation: Conversation,
        approval_id: str,
        user_id: UUID,
        pod_id: UUID,
        decision: AgentRunApprovalDecision,
        response: dict[str, object] | None = None,
        agent_name: str | None = None,
        defer_reconciliation: bool = False,
    ) -> ApprovalResolution:
        """Resume a paused run for an already-authorized + loaded conversation.

        Idempotent and self-healing. Classifies the approval three ways:
          * neither a paused call nor a recorded decision exists -> it is unknown
            (``UnknownApprovalError`` -> 404);
          * no decision yet -> record it (the unique (conversation, approval) row
            is the double-submit lock), then reconcile;
          * a decision already exists -> adopt it and reconcile (the retry /
            self-heal path) instead of erroring.

        The reconcile step is safe to call repeatedly: the approved tool runs at
        most once (guarded by an existing tool return) and the resume run starts
        at most once. Callers MUST have authorized the resolver against this
        conversation first. The caller's current auth context must be the
        conversation owner's, since an approved ``request_approval`` runs the
        wrapped tool with that authority.

        ``defer_reconciliation`` is for callers under a deadline (an HTTP
        request, a platform webhook): the decision still commits here, but the
        slow half is handed to a worker job and the status comes back
        ``"queued"``.
        """
        decision_row = await self.conversation_repository.get_approval_decision(
            conversation_id=conversation.id,
            approval_id=approval_id,
        )
        paused = await self._paused_call_from_messages(
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
            # Fresh resolve: record the decision. The unique (conversation,
            # approval) row locks out a concurrent double-submit before any
            # side-effecting tool runs.
            decision_tool_name = (
                "ask_user"
                if kind == "ask_user"
                else str(tool_args.get("tool_name") or "request_approval")
            )
            recorded = await self.conversation_repository.record_approval_decision(
                conversation_id=conversation.id,
                approval_id=approval_id,
                agent_run_id=paused_run_id,
                tool_name=decision_tool_name,
                decision=decision,
                response=response or {},
                resolved_by_user_id=user_id,
            )
            await self.uow.commit()
            if recorded:
                effective_decision, effective_response, status = (
                    decision,
                    response or {},
                    "resolved",
                )
            else:
                # A concurrent resolve won the race; adopt its stored decision and
                # reconcile (idempotent) rather than raising "already resolved".
                stored = await self.conversation_repository.get_approval_decision(
                    conversation_id=conversation.id,
                    approval_id=approval_id,
                )
                effective_decision, effective_response = (
                    stored if stored is not None else (decision, response or {})
                )
                status = "reconciled"
        else:
            # Decision already recorded (retry / self-heal). Do NOT re-record or
            # re-run the tool; adopt the stored decision and finish whatever the
            # prior attempt left undone.
            effective_decision, effective_response = decision_row
            status = "reconciled"

        existing_return = (
            await self.conversation_repository.get_tool_return(
                conversation_id=conversation.id,
                tool_call_id=approval_id,
            )
            if defer_reconciliation
            else None
        )
        if should_defer_approved_tool(
            defer_reconciliation=defer_reconciliation,
            kind=kind,
            tool_args=tool_args,
            decision=effective_decision,
            has_tool_return=existing_return is not None,
        ):
            # Deferred to after the commit, for two reasons. The connection is
            # the smaller one: enqueuing is a Redis round trip and this runs
            # inside the caller's transaction. The larger one is ordering -- an
            # enqueue that happens before the commit queues a job against state
            # that a rollback would erase, and the worker would then reconcile
            # an approval the database never accepted.
            self.uow.after_commit(
                lambda: queue_approval_reconciliation(
                    conversation_id=conversation.id,
                    approval_id=approval_id,
                    user_id=user_id,
                    pod_id=pod_id,
                )
            )
            return ApprovalResolution(status="queued", decision=effective_decision)

        await self._reconcile_resume(
            conversation=conversation,
            approval_id=approval_id,
            paused_run_id=paused_run_id,
            kind=kind,
            tool_args=tool_args,
            decision=effective_decision,
            response=effective_response,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        return ApprovalResolution(status=status, decision=effective_decision)

    async def _claim_execution(
        self, *, conversation_id: UUID, approval_id: str
    ) -> bool:
        """Take the right to run this approval's tool, durably, before running it.

        Committed immediately and on its own. An uncommitted claim is not a
        claim: the whole point is that a worker killed mid-execution leaves
        evidence behind, and evidence inside a transaction that dies with the
        worker is no evidence at all.

        Returns False when somebody already holds it, which is how a retried
        reconcile job skips an execution that already happened.
        """
        claimed = await self.conversation_repository.claim_approval_execution(
            conversation_id=conversation_id,
            approval_id=approval_id,
        )
        if claimed:
            await self.uow.commit()
        return claimed

    async def _reconcile_resume(
        self,
        *,
        conversation: Conversation,
        approval_id: str,
        paused_run_id: UUID,
        kind: str,
        tool_args: dict[str, object],
        decision: AgentRunApprovalDecision,
        response: dict[str, object],
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None,
    ) -> None:
        """Finish (or re-finish) an approval's resume; safe to call repeatedly.

        Two idempotent halves:
          1. Synthesize + persist the paused call's tool return exactly once. If a
             return already exists the approved tool has already run, so we skip
             the rebuild entirely — re-executing it (e.g. re-deploying an app, or
             re-recording session grants) would be a correctness bug.
          2. Start the resume run only once every pausing call in the paused run is
             resolved and no run is already active. The conversation lock
             serializes this so two near-simultaneous resolves don't each start a
             run.

        A resolve that died mid-flight (decision committed, return not appended,
        or run not started) self-heals when this runs again on the user's retry.
        """
        existing_return = await self.conversation_repository.get_tool_return(
            conversation_id=conversation.id,
            tool_call_id=approval_id,
        )
        if existing_return is None and await self._claim_execution(
            conversation_id=conversation.id, approval_id=approval_id
        ):
            # Build the return the resumed run will replay. This runs the wrapped
            # tool as the user, so it must happen at most once — hence a claim
            # above rather than only the read below it. The append re-checks
            # cheaply and closes the remaining window.
            return_tool_name, tool_result = await self.resume_returns.build(
                conversation=conversation,
                user_id=user_id,
                kind=kind,
                tool_args=tool_args,
                decision=decision,
                response=response,
                paused_agent_run_id=paused_run_id,
            )
            await self.pauses.append_pause_tool_return(
                conversation=conversation,
                paused_run_id=paused_run_id,
                tool_call_id=approval_id,
                tool_name=return_tool_name,
                tool_result=tool_result,
            )

        if agent_host_permission_request(tool_args) is not None:
            # An Agent Host pauses *inside* a live run: the decision was just
            # handed to the host, which carries the same run on from where it
            # stopped. Starting a resume run here would dispatch a second,
            # duplicate host run for the same turn.
            return

        if await self.pauses.resume_would_duplicate_a_live_turn(paused_run_id):
            return

        await self.pauses.start_resume_run_if_ready(
            conversation=conversation,
            paused_run_id=paused_run_id,
            resumed_tool_call_id=approval_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
            source="approval_resume",
        )

    async def supersede_stale_pending_interactions(
        self,
        *,
        conversation: Conversation,
        user_id: UUID,
    ) -> list[Message]:
        """Auto-deny any ask_user/request_approval call left unresolved from an
        earlier WAITING pause, before starting a fresh run for a new message.

        This only ever runs when no run is currently active (the caller checked
        that already), so any pausing call still unresolved at this point belongs
        to a run that already finished — the user moved on without answering it.
        Always DENY, never approve: this is a safety fallback synthesizing a
        response on the user's behalf, not a real decision, so a request_approval
        must never auto-execute here. Writes ride the caller's transaction (no
        commit here) so they land atomically with the new run/message it creates;
        the caller is responsible for publishing the returned messages once that
        transaction actually commits. That also rules out side effects Lemma
        could not take back on a rollback, which is why an Agent Host permission
        is not delivered from here.
        """
        resolved_ids = await self.conversation_repository.list_resolved_approval_ids(
            conversation_id=conversation.id
        )
        messages, _ = await self.conversation_repository.list_messages(
            conversation_id=conversation.id,
            limit=500,
        )
        stale = [
            message
            for message in messages
            if message.kind == MessageKind.TOOL_CALL
            and message.tool_name in PAUSING_TOOL_NAMES
            and message.tool_call_id is not None
            and message.tool_call_id not in resolved_ids
        ]
        synthesized_returns: list[Message] = []
        for message in stale:
            saved_return = await self._deny_stale_pending_interaction(
                conversation=conversation,
                message=message,
                user_id=user_id,
            )
            if saved_return is not None:
                synthesized_returns.append(saved_return)
        return synthesized_returns

    async def _deny_stale_pending_interaction(
        self,
        *,
        conversation: Conversation,
        message: Message,
        user_id: UUID,
    ) -> Message | None:
        # Re-established rather than assumed: the caller's filter proves the
        # call id and the tool name, but none of that survives being passed as a
        # `Message`. A call missing any of the three cannot have a faithful
        # return synthesized for it.
        tool_call_id = message.tool_call_id
        tool_name = message.tool_name
        agent_run_id = message.agent_run_id
        if tool_call_id is None or tool_name is None or agent_run_id is None:
            # Only the run id is genuinely unproven here, and losing it is not
            # cosmetic: the pause stays unresolved, so the next run rebuilds a
            # history still showing an open question nobody can now answer.
            # Said out loud because the alternative is a conversation that
            # quietly stops making sense.
            logger.warning(
                "agent.conversation_approvals.pause_without_run_skipped.degraded",
                conversation_id=str(conversation.id),
                tool_call_id=tool_call_id,
                tool_name=tool_name,
            )
            return None
        tool_args = message.tool_args if isinstance(message.tool_args, dict) else {}
        decision_tool_name = (
            "ask_user"
            if tool_name == "ask_user"
            else str(tool_args.get("tool_name") or "request_approval")
        )
        response: JsonObject = {"superseded_by_new_message": True}
        recorded = await self.conversation_repository.record_approval_decision(
            conversation_id=conversation.id,
            approval_id=tool_call_id,
            agent_run_id=agent_run_id,
            tool_name=decision_tool_name,
            decision=AgentRunApprovalDecision.DENY,
            response=response,
            resolved_by_user_id=user_id,
        )
        if not recorded:
            # A concurrent resolve already recorded a real decision for this call;
            # that resolve (or its own reconcile) owns synthesizing the return.
            return None
        existing_return = await self.conversation_repository.get_tool_return(
            conversation_id=conversation.id,
            tool_call_id=tool_call_id,
        )
        if existing_return is not None:
            return None
        return_tool_name, tool_result = await self.resume_returns.build(
            conversation=conversation,
            user_id=user_id,
            kind=tool_name,
            tool_args=tool_args,
            decision=AgentRunApprovalDecision.DENY,
            response=response,
            paused_agent_run_id=agent_run_id,
            deliver_to_host=False,
        )
        return await self.conversation_repository.append_message(
            conversation_id=conversation.id,
            agent_run_id=agent_run_id,
            draft=MessageDraft.of_tool_return(
                tool_call_id=tool_call_id,
                tool_name=return_tool_name,
                tool_result=tool_result,
            ),
        )

    async def _paused_call_from_messages(
        self,
        *,
        conversation_id: UUID,
        approval_id: str,
    ) -> PausedCall | None:
        """The pausing tool call for an approval, regardless of decision state.

        Addressed directly by ``tool_call_id`` (not a message-window scan) so a
        long conversation can't hide the original call during resume
        reconciliation.
        """
        message = await self.conversation_repository.get_tool_call(
            conversation_id=conversation_id,
            tool_call_id=approval_id,
        )
        if (
            message is None
            or message.tool_name is None
            or message.tool_name not in PAUSING_TOOL_NAMES
            or message.agent_run_id is None
        ):
            return None
        tool_args = message.tool_args if isinstance(message.tool_args, dict) else {}
        return PausedCall(
            agent_run_id=message.agent_run_id,
            kind=message.tool_name,
            tool_args=tool_args,
        )

    async def oldest_unresolved_pause(
        self,
        *,
        conversation_id: UUID,
        tool_names: Sequence[str],
    ) -> dict[str, object] | None:
        resolved_ids = await self.conversation_repository.list_resolved_approval_ids(
            conversation_id=conversation_id
        )
        messages, _ = await self.conversation_repository.list_messages(
            conversation_id=conversation_id,
            limit=500,
        )
        for message in messages:
            if (
                message.kind == MessageKind.TOOL_CALL
                and message.tool_name in tool_names
                and message.tool_call_id is not None
                and message.tool_call_id not in resolved_ids
            ):
                return {
                    "tool_call_id": message.tool_call_id,
                    "kind": message.tool_name,
                    "tool_args": (
                        message.tool_args if isinstance(message.tool_args, dict) else {}
                    ),
                    "agent_run_id": message.agent_run_id,
                }
        return None
