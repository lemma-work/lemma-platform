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
from app.modules.agent.domain.approvals import (
    ApprovalResolution,
    ApprovalResolutionStatus,
)
from app.modules.agent.domain.entities import Conversation, Message
from app.modules.agent.domain.errors import (
    ApprovalNotOwnedError,
    UnknownApprovalError,
)
from app.modules.agent.domain.pausing_tools import (
    PAUSING_TOOL_NAMES,
    PausingKind,
    PendingPause,
    parked_suspension_of,
)
from app.modules.agent.infrastructure.db.approvals import ApprovalDecisions
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    JsonObject,
)
from app.modules.agent.services.conversation_resume_return import (
    ResumeToolReturnBuilder,
)
from app.modules.agent.services.approval_reconciliation import (
    runs_in_worker,
    stage_approval_execution,
)
from app.modules.agent.services.pause_resume import PauseResume
from app.modules.agent.services.pause_supersession import (
    supersede_stale_pending_interactions,
)

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
    kind: PausingKind
    tool_args: JsonObject


class ApprovalCoordinator:
    """Turns a resolved pause into a persisted decision and a resumed run."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        resume_returns: ResumeToolReturnBuilder,
        pauses: PauseResume,
    ) -> None:
        self.uow = uow
        self.conversations = Conversations(uow)
        self.runs = Runs(uow)
        self.messages = Messages(uow)
        self.pausing_calls = Pauses(uow)
        self.approval_decisions = ApprovalDecisions(uow)
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
        most once (guarded by an execution claim and an existing tool return)
        and the resume run starts at most once. Callers MUST have authorized the
        resolver against this conversation first.

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
        paused = await self.paused_call(
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
            if kind != "ask_user" and user_id != conversation.user_id:
                # An approved call runs with the owner's authority (see
                # `ApprovedExecution`), so only the owner may decide it. Every
                # entry point checks this already; this is the one place all
                # of them pass through, so it is enforced here too.
                raise ApprovalNotOwnedError()
            # Fresh resolve: record the decision. The unique (conversation,
            # approval) row locks out a concurrent double-submit before any
            # side-effecting tool runs.
            decision_tool_name = (
                "ask_user"
                if kind == "ask_user"
                else str(tool_args.get("tool_name") or "request_approval")
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

        await self.reconcile_resume(
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

    async def reconcile_resume(
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
        """Finish (or re-finish) a decision that runs nothing; safe to repeat.

        Two idempotent halves:
          1. Synthesize + persist the paused call's tool return exactly once.
             Only decisions that run nothing reach here -- an answer, a denial,
             an Agent Host permission (a queued command), or one whose return
             already exists; an approved tool is the worker's
             (``runs_in_worker``).
          2. Start the resume run only once every pausing call in the paused run is
             resolved and no run is already active. The conversation lock
             serializes this so two near-simultaneous resolves don't each start a
             run.

        A resolve that died mid-flight (decision committed, return not appended,
        or run not started) self-heals when this runs again on the user's retry.
        """
        # Two resolvers adopting one decision (a tap and a click) both get here;
        # the conversation lock makes the second see the first one's return.
        await self.conversations.lock_conversation(conversation.id)
        existing_return = await self.pausing_calls.get_tool_return(
            conversation_id=conversation.id,
            tool_call_id=approval_id,
        )
        if existing_return is None:
            return_tool_name, tool_result = await self.resume_returns.build(
                conversation=conversation,
                user_id=user_id,
                kind=kind,
                tool_args=tool_args,
                decision=decision,
                response=response,
                paused_agent_run_id=paused_run_id,
                tool_call_id=approval_id,
            )
            await self.pauses.append_pause_tool_return(
                conversation=conversation,
                paused_run_id=paused_run_id,
                tool_call_id=approval_id,
                tool_name=return_tool_name,
                tool_result=tool_result,
            )
        await self.resume_after_return(
            conversation=conversation,
            approval_id=approval_id,
            paused_run_id=paused_run_id,
            tool_args=tool_args,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
        )

    async def resume_after_return(
        self,
        *,
        conversation: Conversation,
        approval_id: str,
        paused_run_id: UUID,
        tool_args: JsonObject,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None,
    ) -> None:
        """Start the resume run for a pause whose return is now written."""
        host_permission = agent_host_permission_request(tool_args) is not None
        if host_permission and not await self._turn_ended_while_parked(paused_run_id):
            # An Agent Host pauses *inside* a live run: the decision was just
            # handed to the host, which carries the same run on from where it
            # stopped. Starting a resume run here would dispatch a second,
            # duplicate host run for the same turn. Unless Lemma ended that
            # turn because the request sat past its parked window: then the
            # decision reaches the agent only by resuming, like any pause.
            return

        if await self.pauses.resume_would_duplicate_a_live_turn(
            paused_run_id, conversation_id=conversation.id, tool_call_id=approval_id
        ):
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

    async def _turn_ended_while_parked(self, paused_run_id: UUID) -> bool:
        run = await self.runs.get_agent_run(paused_run_id)
        return run is not None and parked_suspension_of(run.metadata) is not None

    async def record_unconfirmed_execution(
        self,
        *,
        conversation: Conversation,
        approval_id: str,
        paused: PausedCall,
        decision: AgentRunApprovalDecision,
        response: JsonObject,
        claimed: bool,
    ) -> bool:
        """Write the honest return for an approval whose execution never reported.

        ``claimed``: an executor took the one-shot claim and then lost its
        lease -- killed, out of memory, partitioned -- so the tool may or may
        not have run, and it is never run again: an approved tool runs with a
        person's authority, and running it twice is worse than saying so.
        Unclaimed: it kept failing before it started, so nothing ran.

        Returns whether this wrote the return (False: one already existed).
        """
        logger.error(
            "agent.conversation_approvals.approved_tool_never_confirmed.failed",
            conversation_id=str(conversation.id),
            approval_id=approval_id,
            claimed=claimed,
        )
        # Lazy: the tool models import the tool registry, which imports back
        # through this module's reconciliation helpers.
        from app.modules.agent.tools.user_interaction.models import (
            RequestApprovalResponse,
        )

        error = (
            "This was approved, but the worker running it stopped before it "
            "reported a result, and Lemma cannot re-run an approved action. "
            "Check whether it took effect before asking again."
            if claimed
            else (
                "This was approved, but Lemma could not start it, so it did not "
                "run. Ask again if it is still needed."
            )
        )
        return await self.pauses.append_pause_tool_return(
            conversation=conversation,
            paused_run_id=paused.agent_run_id,
            tool_call_id=approval_id,
            tool_name=paused.kind.value,
            tool_result=RequestApprovalResponse(
                success=False,
                error=error,
                decision=decision,
                executed=False,
                response=response,
            ).model_dump(mode="json"),
        )

    async def supersede_stale_pending_interactions(
        self,
        *,
        conversation: Conversation,
        user_id: UUID,
    ) -> list[Message]:
        """DENY every pause nobody decided; see ``pause_supersession``.

        A ``wait_for`` the person typed past is cancelled first, with its timer,
        in the same (caller's) transaction: left ACTIVE, its timer would later
        fire into a conversation that had moved on and start a resume nobody
        asked for.
        """
        cancelled = await self._cancel_superseded_wait(conversation)
        superseded = await supersede_stale_pending_interactions(
            pausing_calls=self.pausing_calls,
            approval_decisions=self.approval_decisions,
            messages=self.messages,
            resume_returns=self.resume_returns,
            conversation=conversation,
            user_id=user_id,
        )
        return [*cancelled, *superseded]

    async def _cancel_superseded_wait(
        self, conversation: Conversation
    ) -> list[Message]:
        # Lazy: the wait service reaches the conversation service, which
        # constructs this coordinator.
        from app.core.infrastructure.work import WorkWriter
        from app.modules.agent.domain.pausing_tools import WAIT_TOOL_NAME
        from app.modules.agent.domain.value_objects import MessageDraft
        from app.modules.agent.domain.wait import AgentWaitWakeReason
        from app.modules.agent.infrastructure.wait_repository import (
            AgentConversationWaitRepository,
        )
        from app.modules.agent.services.wait_timers import disarm_wait
        from app.modules.agent.tools.waiting.models import (
            build_wait_result,
            elapsed_seconds,
        )

        waits = AgentConversationWaitRepository(self.uow)
        active = await waits.find_active_for_conversation(conversation.id)
        if active is None:
            return []
        # Conversation, then wait: the order every wait ending takes.
        await self.conversations.lock_conversation(conversation.id)
        wait = await waits.claim(active.id)
        if wait is None or not wait.cancel():
            return []
        await waits.update(wait)
        await disarm_wait(WorkWriter.of(self.uow), wait)
        existing = await self.pausing_calls.get_tool_return(
            conversation_id=conversation.id, tool_call_id=wait.tool_call_id
        )
        if existing is not None:
            return []
        spec = wait.wait_spec
        saved = await self.messages.append_message(
            conversation_id=conversation.id,
            agent_run_id=wait.agent_run_id,
            draft=MessageDraft.of_tool_return(
                tool_call_id=wait.tool_call_id,
                tool_name=WAIT_TOOL_NAME,
                tool_result=build_wait_result(
                    wait_type=wait.wait_type,
                    reason=AgentWaitWakeReason.CANCELLED,
                    waited_seconds=elapsed_seconds(spec.started_at),
                    note_to_self=spec.note_to_self,
                ),
            ),
        )
        return [saved]

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

    async def paused_call(
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
        message = await self.pausing_calls.get_tool_call(
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
            kind=PausingKind(message.tool_name),
            tool_args=tool_args,
        )

    async def oldest_unresolved_pause(
        self,
        *,
        conversation_id: UUID,
        tool_names: Sequence[str],
    ) -> PendingPause | None:
        """The pause a typed reply routes to, or the card renders.

        Asked in SQL rather than by scanning the newest 500 messages: past that
        window the conversation reported itself as waiting on nothing, so the
        approval card vanished and a reply had nowhere to go.
        """
        pending = await self.pausing_calls.pausing_calls_awaiting_a_decision(
            conversation_id=conversation_id,
            pausing_tool_names=tool_names,
            limit=1,
        )
        if not pending:
            return None
        message = pending[0]
        return PendingPause(
            tool_call_id=message.tool_call_id or "",
            kind=PausingKind(message.tool_name),
            tool_args=(
                message.tool_args if isinstance(message.tool_args, dict) else {}
            ),
            agent_run_id=message.agent_run_id,
        )
