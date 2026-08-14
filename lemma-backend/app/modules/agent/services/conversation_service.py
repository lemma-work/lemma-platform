"""Conversation service for unified agent chats."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import NamedTuple
from uuid import UUID


from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.current import get_current_context
from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_ID,
    DEFAULT_POD_AGENT_NAME,
)
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.entities import (
    Agent,
    AgentRun,
    Conversation,
    Message,
)
from app.modules.agent.domain.errors import (
    AgentNotFoundError,
    ConversationNotFoundError,
    UnknownApprovalError,
)
from app.modules.agent.domain.events import (
    AgentRunStartedEvent,
    AgentRunStopRequestedEvent,
)
from app.modules.agent.domain.ports import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    AgentRunStartResult,
    AgentRunStatus,
    AgentRuntimeConfig,
    ConversationAgentSelection,
    ConversationStatus,
    ConversationType,
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent.domain.agent_host_permissions import (
    agent_host_permission_request,
)
from app.modules.agent.services.approval_reconciliation import (
    agent_host_permission_tool_return,
    execute_approved_tool_as_user,
    pending_user_approval_messages,
    queue_approval_reconciliation,
    record_session_approvals,
    should_defer_approved_tool,
)
from app.modules.agent.services.runtime_profile_service import (
    DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
)
from app.modules.agent.services.realtime import (
    input_added_payload,
    message_payload,
    publish_conversation_event,
)
from app.modules.agent.services.serialization import message_to_payload
from app.modules.agent.services.workspace_location import (
    apply_location_metadata,
    resolve_workspace_location,
)
from app.modules.pod.contracts import PodConfig
from app.composition.agent_pod import create_agent_pod_repository
from app.composition.agent_usage import UsageLimitExceededError, UsageService
from app.composition.agent_snooze_scheduler import cancel_snooze_wake
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.pause_resume import (
    PAUSING_TOOL_NAMES,
    PauseResumeMixin,
)
from app.modules.agent.tools.snooze.models import (
    build_snooze_result,
    elapsed_seconds,
)

_POD_ASSISTANT_AGENT_ID = DEFAULT_POD_AGENT_ID

# Defined with the primitive that consumes it, so the list and the resume path
# that depends on it cannot drift apart.
_PAUSING_TOOL_NAMES = PAUSING_TOOL_NAMES


class ApprovalResolution(NamedTuple):
    """Approval status plus the authoritative (stored) decision.

    ``status`` is ``"resolved"`` when this call recorded the decision,
    ``"reconciled"`` when it only finished a prior half-done resume (the
    self-heal path), or ``"queued"`` when the decision is durable and a worker
    job owns the rest.
    """

    status: str
    decision: AgentRunApprovalDecision


# When set, starting a new agent run does NOT publish the AgentRunStartedEvent
# that hands execution to the streaq worker. The caller takes responsibility for
# executing the run itself. Used by the surface e2e suite, which runs the agent
# in-process (to deliver via the in-test fake platform servers) and would
# otherwise double-run it with the shared session worker.
_SUPPRESS_RUN_ENQUEUE: ContextVar[bool] = ContextVar(
    "suppress_agent_run_enqueue", default=False
)


@contextmanager
def suppress_agent_run_enqueue() -> Iterator[None]:
    """Run agent-run starts inline: skip the worker-dispatch event publish."""
    token = _SUPPRESS_RUN_ENQUEUE.set(True)
    try:
        yield
    finally:
        _SUPPRESS_RUN_ENQUEUE.reset(token)


class _Unset:
    pass


_UNSET = _Unset()


class ConversationService(PauseResumeMixin):
    """Application service for conversation storage and run coordination."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        conversation_repository: ConversationRepository,
        agent_repository: AgentRepository,
        authorization_service: object,
        fallback_model_name: str | None = None,
        usage_service: UsageService | None = None,
    ):
        self.uow = uow
        self.conversation_repository = conversation_repository
        self.agent_repository = agent_repository
        self.authorization_service = authorization_service
        self.fallback_model_name = fallback_model_name
        self.usage_service = usage_service

    async def create_conversation(
        self,
        *,
        pod_id: UUID,
        agent_name: str | None,
        user_id: UUID,
        title: str | None = None,
        instructions: str | None = None,
        agent_runtime: AgentRuntimeConfig | None = None,
        parent_id: UUID | None = None,
        type: ConversationType = ConversationType.CHAT,
        metadata: dict[str, object] | None = None,
        require_execute_grant: bool = True,
    ) -> Conversation:
        organization_id = await self._get_pod_organization_id(pod_id)
        agent = (
            await self._resolve_agent_for_path(pod_id=pod_id, agent_name=agent_name)
            if agent_name is not None
            else None
        )
        # require_execute_grant is False only for self-spawn (an agent launching
        # another instance of itself) — see SubAgentService.spawn. An agent has no
        # agent.execute grant on itself, but running another copy of the agent the
        # user is already running is no privilege escalation.
        if require_execute_grant:
            # Dispatching a *named* agent checks agent.execute and agent.read
            # together. agent.execute implies agent.read (IMPLIED_PERMISSIONS),
            # so an execute-only grant satisfies both; checking read here anyway
            # warms the decision cache for the run-load that follows and keeps
            # the error complete for callers missing everything. The default
            # pod agent (agent is None) needs only execute.
            actions = [Permissions.AGENT_EXECUTE]
            if agent is not None:
                actions.append(Permissions.AGENT_READ)
            await self._require_agent_actions(
                user_id=user_id,
                pod_id=pod_id,
                agent_id=agent.id if agent else None,
                actions=actions,
            )
        # A PROJECT is an explicit user choice (a pinned group); never coerce it.
        # Otherwise a structured-output agent implies a TASK conversation.
        if type == ConversationType.PROJECT:
            conversation_type = ConversationType.PROJECT
        elif agent is not None and agent.output_schema:
            conversation_type = ConversationType.TASK
        else:
            conversation_type = type

        conversation = Conversation(
            user_id=user_id,
            pod_id=pod_id,
            organization_id=organization_id,
            agent_id=agent.id if agent else None,
            title=title,
            instructions=instructions,
            agent_runtime=agent_runtime,
            parent_id=parent_id,
            type=conversation_type,
            metadata=dict(metadata) if metadata else {},
        )
        await self._apply_inherited_cwd(conversation, parent_id=parent_id)
        return await self.conversation_repository.create_conversation(conversation)

    async def _apply_inherited_cwd(
        self,
        conversation: Conversation,
        *,
        parent_id: UUID | None,
    ) -> None:
        """Record where this conversation works, once, at creation.

        The rules — inheritance, an explicit cwd, a project repo — all live in
        ``workspace_location.apply_location_metadata``, which is also what reads
        the result back. The parent is fetched lazily because most conversations
        settle their location without ever needing one.
        """

        async def _parent() -> Conversation | None:
            if parent_id is None:
                return None
            return await self.conversation_repository.get_conversation(parent_id)

        await apply_location_metadata(conversation, fetch_parent=_parent)

    async def list_conversations(
        self,
        *,
        pod_id: UUID,
        agent_selection: ConversationAgentSelection[str],
        user_id: UUID,
        status: ConversationStatus | None = None,
        type: ConversationType | None = None,
        metadata_filters: dict[str, object] | None = None,
        parent_id: UUID | None = None,
        cursor: UUID | None = None,
        limit: int = 20,
    ) -> tuple[list[Conversation], UUID | None]:
        expected_agent_id = await self._expected_agent_id(
            pod_id=pod_id,
            agent_name=agent_selection.value,
        )
        resolved_selection = agent_selection.resolve(expected_agent_id)
        await self._require_agent_action(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
            action=Permissions.AGENT_READ,
        )
        return await self.conversation_repository.list_conversations(
            user_id=user_id,
            pod_id=pod_id,
            agent_selection=resolved_selection,
            status=status,
            conversation_type=type,
            metadata_filters=metadata_filters,
            parent_id=parent_id,
            cursor=cursor,
            limit=limit,
        )

    async def get_conversation(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
        require_read_grant: bool = True,
    ) -> Conversation:
        expected_agent_id = await self._expected_agent_id(
            pod_id=pod_id,
            agent_name=agent_name,
        )
        # The latest run carries the failure diagnostics and the retry decision.
        conversation = await self.conversation_repository.get_conversation(
            conversation_id,
            include_runs=True,
        )
        self._validate_conversation_access(
            conversation,
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
        )
        # require_read_grant is False only for self-spawn (an agent operating on
        # its own conversation tree): ownership is validated above, but the agent
        # holds no agent.read grant on itself, so skip the cross-agent grant check.
        if require_read_grant:
            await self._require_agent_action(
                user_id=user_id,
                pod_id=pod_id,
                agent_id=conversation.agent_id,
                action=Permissions.AGENT_READ,
            )
        conversation.last_run_retryable = await self._latest_run_is_retryable(
            conversation
        )
        return conversation

    async def _latest_run_is_retryable(self, conversation: Conversation) -> bool:
        """Whether the newest run can be replayed without duplicating output.

        The status check runs first and short-circuits: a run that did not fail
        is not retryable whatever its messages say, and that is nearly every
        conversation, so the message query below is rarely reached. Asking the
        database that question directly is what replaced eager-loading the
        whole transcript to evaluate it in Python.
        """
        latest = conversation.agent_runs[-1] if conversation.agent_runs else None
        if latest is None or latest.status != AgentRunStatus.FAILED:
            return False
        return await self.conversation_repository.run_has_only_user_messages(
            latest.id
        )

    async def update_conversation(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
        title: str | None | _Unset = _UNSET,
        instructions: str | None | _Unset = _UNSET,
        agent_runtime: AgentRuntimeConfig | None | _Unset = _UNSET,
        metadata: dict[str, object] | None | _Unset = _UNSET,
    ) -> Conversation:
        expected_agent_id = await self._expected_agent_id(
            pod_id=pod_id,
            agent_name=agent_name,
        )
        conversation = await self.conversation_repository.get_conversation(
            conversation_id
        )
        self._validate_conversation_access(
            conversation,
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
        )
        if conversation is None:
            raise ConversationNotFoundError()
        await self._require_agent_action(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=conversation.agent_id,
            action=Permissions.AGENT_EXECUTE,
        )

        if not isinstance(title, _Unset):
            conversation.title = title
        if not isinstance(instructions, _Unset):
            conversation.instructions = instructions
        if not isinstance(agent_runtime, _Unset):
            conversation.agent_runtime = agent_runtime
        if not isinstance(metadata, _Unset):
            conversation.metadata = metadata

        return await self.conversation_repository.update_conversation(conversation)

    async def list_messages(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
        before_sequence: int | None = None,
        after_sequence: int | None = None,
        limit: int = 100,
    ) -> tuple[list[Message], int | None]:
        expected_agent_id = await self._expected_agent_id(
            pod_id=pod_id,
            agent_name=agent_name,
        )
        conversation = await self.conversation_repository.get_conversation(
            conversation_id
        )
        self._validate_conversation_access(
            conversation,
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
        )
        await self._require_agent_action(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=conversation.agent_id,
            action=Permissions.AGENT_READ,
        )
        return await self.conversation_repository.list_messages(
            conversation_id=conversation_id,
            before_sequence=before_sequence,
            after_sequence=after_sequence,
            limit=limit,
        )

    async def get_active_agent_run(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
    ) -> AgentRun | None:
        expected_agent_id = await self._expected_agent_id(
            pod_id=pod_id,
            agent_name=agent_name,
        )
        conversation = await self.conversation_repository.get_conversation(
            conversation_id
        )
        self._validate_conversation_access(
            conversation,
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
        )
        await self._require_agent_action(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=conversation.agent_id,
            action=Permissions.AGENT_READ,
        )
        return await self.conversation_repository.get_active_agent_run(conversation_id)

    async def list_user_approvals(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
    ) -> list[Message]:
        conversation = await self._authorized_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
            action=Permissions.AGENT_READ,
        )
        messages, _ = await self.conversation_repository.list_messages(
            conversation_id=conversation.id,
            limit=500,
        )
        return pending_user_approval_messages(messages)

    async def resolve_user_approval(
        self,
        *,
        conversation_id: UUID,
        approval_id: str,
        user_id: UUID,
        pod_id: UUID,
        decision: AgentRunApprovalDecision,
        response: dict[str, object] | None = None,
        agent_name: str | None = None,
        defer_reconciliation: bool = False,
    ) -> ApprovalResolution:
        """Record the user's decision and resume the paused agent run.

        ``ask_user`` / ``request_approval`` end their run when called (conversation
        -> WAITING) instead of blocking. This records the decision durably, then
        synthesizes the tool's return (the answers, or the approved tool's result
        run as the user, or a denial) and starts a fresh run that replays it from
        history so the agent continues where it left off.

        This is the HTTP entry point: it authorizes the caller, then delegates to
        :meth:`resolve_user_approval_internal`. Surface ingress (which has already
        authorized the external user against the conversation owner) calls the
        internal method directly with the loaded conversation.
        """
        conversation = await self._authorized_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
            action=Permissions.AGENT_EXECUTE,
        )
        return await self.resolve_user_approval_internal(
            conversation=conversation,
            approval_id=approval_id,
            user_id=user_id,
            pod_id=pod_id,
            decision=decision,
            response=response,
            agent_name=agent_name,
            defer_reconciliation=defer_reconciliation,
        )

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
        kind = str(paused["kind"])
        tool_args = paused["tool_args"] if isinstance(paused["tool_args"], dict) else {}
        paused_run_id = paused["agent_run_id"]

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

        await self._reconcile_approval_resume(
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

    async def _reconcile_approval_resume(
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
        if existing_return is None:
            # Build the return the resumed run will replay. This check guards the
            # *build*, which for an approved request_approval runs the wrapped tool
            # as the user — re-executing it (re-deploying an app, re-recording
            # session grants) would be a correctness bug. The append below re-checks
            # cheaply and closes the race.
            return_tool_name, tool_result = await self._build_resume_tool_return(
                conversation=conversation,
                user_id=user_id,
                kind=kind,
                tool_args=tool_args,
                decision=decision,
                response=response,
                paused_agent_run_id=paused_run_id,
            )
            await self.append_pause_tool_return(
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

        await self.start_resume_run_if_ready(
            conversation=conversation,
            paused_run_id=paused_run_id,
            resumed_tool_call_id=approval_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
            source="approval_resume",
        )

    async def _build_resume_tool_return(
        self,
        *,
        conversation: Conversation,
        user_id: UUID,
        kind: str,
        tool_args: dict[str, object],
        decision: AgentRunApprovalDecision,
        response: dict[str, object],
        paused_agent_run_id: UUID,
        deliver_to_host: bool = True,
    ) -> tuple[str, object]:
        """Return ``(tool_name, tool_result)`` for the synthesized resume message."""
        from app.modules.agent.tools.user_interaction.models import (
            AskUserResponse,
            RequestApprovalResponse,
        )

        if kind == "ask_user":
            if decision == AgentRunApprovalDecision.DENY:
                content = AskUserResponse(
                    success=False,
                    message="User dismissed the questions without answering.",
                )
            else:
                answers: dict[str, object] = {}
                candidate = response.get("answers")
                if isinstance(candidate, dict):
                    answers = candidate
                elif response:
                    answers = response
                content = AskUserResponse(
                    success=True,
                    answers=answers,
                    message="User answered the questions.",
                )
            return "ask_user", content.model_dump(mode="json")

        host_permission = agent_host_permission_request(tool_args)
        if host_permission is not None and deliver_to_host:
            # Checked before the denial branch below: a denial must reach the
            # host too, or its ACP agent sits blocked until the request times
            # out half an hour later.
            return "request_approval", await agent_host_permission_tool_return(
                request=host_permission,
                agent_run_id=paused_agent_run_id,
                decision=decision,
                response=response,
            )
        if host_permission is not None:
            # Superseding rides the caller's uncommitted transaction. Handing
            # the decision to the host from here would commit a command in a
            # separate transaction that the caller's rollback could not take
            # back. The run this belonged to is over, so there is nothing to
            # unblock; a host still executing an orphaned run is stopped by
            # reconcile_agent_host_dispatch, which cancels it outright.
            return "request_approval", RequestApprovalResponse(
                success=False,
                message="The request was superseded before it was answered.",
                decision=decision,
                executed=False,
                response=response,
            ).model_dump(mode="json")

        inner_tool = str(tool_args.get("tool_name") or "")
        inner_args = tool_args.get("args")
        inner_args = inner_args if isinstance(inner_args, dict) else {}
        if decision == AgentRunApprovalDecision.DENY:
            content = RequestApprovalResponse(
                success=False,
                message=f"User denied running {inner_tool}.",
                decision=decision,
                executed=False,
                response=response,
            )
            return "request_approval", content.model_dump(mode="json")

        if decision == AgentRunApprovalDecision.APPROVE_FOR_SESSION:
            # Beyond the one-off run below, remember the approval so the
            # workload can keep performing this action type in this
            # conversation (the authorizer honors it as an ephemeral grant,
            # which is the only unlock for DESTRUCTIVE_ACTIONS besides an
            # explicit grant). The permission ids ride in the request_approval
            # args, copied by the agent from the denied tool result.
            await record_session_approvals(
                conversation_id=conversation.id,
                agent_id=conversation.agent_id,
                tool_args=tool_args,
                user_id=user_id,
            )

        executed = await self._execute_approved_tool_as_user(
            conversation=conversation,
            user_id=user_id,
            agent_run_id=paused_agent_run_id,
            tool_name=inner_tool,
            args=dict(inner_args),
        )
        if executed["ok"]:
            content = RequestApprovalResponse(
                success=True,
                message=f"Approved; {inner_tool} executed as the user.",
                decision=decision,
                executed=True,
                result=executed["value"],
                response=response,
            )
        else:
            content = RequestApprovalResponse(
                success=False,
                error=f"Approved, but running {inner_tool} failed: {executed['error']}",
                decision=decision,
                executed=False,
                response=response,
            )
        return "request_approval", content.model_dump(mode="json")

    async def _execute_approved_tool_as_user(
        self,
        *,
        conversation: Conversation,
        user_id: UUID,
        agent_run_id: UUID,
        tool_name: str,
        args: dict[str, object],
    ) -> dict[str, object]:
        """Run an approved tool with the user's authority; never raise."""
        deps = await self._build_resume_context(
            conversation=conversation,
            user_id=user_id,
            agent_run_id=agent_run_id,
        )
        return await execute_approved_tool_as_user(
            uow=self.uow,
            deps=deps,
            tool_name=tool_name,
            args=args,
        )

    async def _build_resume_context(
        self,
        *,
        conversation: Conversation,
        user_id: UUID,
        agent_run_id: UUID,
    ):
        """Rebuild the agent run context so an approved tool runs like in-run.

        Mirrors ``AgentRunnerService.execute``'s context build (runtime profile,
        workspace location, configured accounts). Surface delivery context is
        omitted — approval-gated action tools don't deliver to surfaces.
        """
        from app.core.infrastructure.db.session import async_session_maker
        from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
        from app.modules.agent.infrastructure.repositories import (
            AgentRuntimeProfileRepository,
        )
        from app.modules.agent.services.runtime_profile_service import (
            AgentRuntimeProfileService,
        )
        from app.modules.agent.tools.callable_tool_factory import (
            AgentCallableToolFactory,
        )
        from app.modules.agent.tools.context import ConversationContext
        from app.core.crypto import get_secret_cipher
        from app.modules.agent.services.workspace_location import resolve_pod_cwd

        uow_factory = SessionUnitOfWorkFactory(async_session_maker)
        agent = await self._resolve_agent(conversation=conversation, user_id=user_id)
        selected_runtime = (
            conversation.agent_runtime
            or agent.agent_runtime
            or await self._default_agent_runtime_for_pod(pod_id=conversation.pod_id)
        )
        async with uow_factory() as uow:
            profile_service = AgentRuntimeProfileService(
                AgentRuntimeProfileRepository(
                    uow, encryption=get_secret_cipher()
                )
            )
            resolved = await profile_service.resolve(
                runtime=selected_runtime,
                organization_id=conversation.organization_id,
                user_id=user_id,
            )
        configured_accounts = await AgentCallableToolFactory(
            uow_factory
        ).resolve_configured_accounts(agent=agent, user_id=user_id)
        workspace_location = resolve_workspace_location(conversation)
        return ConversationContext(
            user_id=user_id,
            org_id=conversation.organization_id,
            pod_id=conversation.pod_id,
            conversation_id=conversation.id,
            agent_name=agent.name,
            agent_run_id=agent_run_id,
            workload_type="agent",
            workload_id=agent.id,
            configured_accounts=configured_accounts,
            runtime_profile=resolved.public_snapshot(),
            runtime_credentials=resolved.credentials or {},
            workspace_id=workspace_location.workspace_id,
            workspace_cwd=workspace_location.cwd,
            workspace_repo=workspace_location.repo,
            pod_cwd=resolve_pod_cwd(conversation),
        )

    async def _supersede_stale_pending_interactions(
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
            and message.tool_name in _PAUSING_TOOL_NAMES
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
        tool_args = message.tool_args if isinstance(message.tool_args, dict) else {}
        decision_tool_name = (
            "ask_user"
            if message.tool_name == "ask_user"
            else str(tool_args.get("tool_name") or "request_approval")
        )
        response = {"superseded_by_new_message": True}
        recorded = await self.conversation_repository.record_approval_decision(
            conversation_id=conversation.id,
            approval_id=message.tool_call_id,
            agent_run_id=message.agent_run_id,
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
            tool_call_id=message.tool_call_id,
        )
        if existing_return is not None:
            return None
        return_tool_name, tool_result = await self._build_resume_tool_return(
            conversation=conversation,
            user_id=user_id,
            kind=message.tool_name,
            tool_args=tool_args,
            decision=AgentRunApprovalDecision.DENY,
            response=response,
            paused_agent_run_id=message.agent_run_id,
            deliver_to_host=False,
        )
        return await self.conversation_repository.append_message(
            conversation_id=conversation.id,
            agent_run_id=message.agent_run_id,
            draft=MessageDraft.of_tool_return(
                tool_call_id=message.tool_call_id,
                tool_name=return_tool_name,
                tool_result=tool_result,
            ),
        )

    async def _paused_call_from_messages(
        self,
        *,
        conversation_id: UUID,
        approval_id: str,
    ) -> dict[str, object] | None:
        """The pausing tool call for an approval, regardless of decision state.

        Addressed directly by ``tool_call_id`` (not a message-window scan) so a
        long conversation can't hide the original call during resume
        reconciliation. Returns ``{agent_run_id, kind, tool_args}`` or ``None``.
        """
        message = await self.conversation_repository.get_tool_call(
            conversation_id=conversation_id,
            tool_call_id=approval_id,
        )
        if (
            message is None
            or message.tool_name not in _PAUSING_TOOL_NAMES
            or message.agent_run_id is None
        ):
            return None
        tool_args = message.tool_args if isinstance(message.tool_args, dict) else {}
        return {
            "agent_run_id": message.agent_run_id,
            "kind": message.tool_name,
            "tool_args": tool_args,
        }

    async def _pending_user_approval_from_messages(
        self,
        *,
        conversation_id: UUID,
        approval_id: str,
    ) -> dict[str, object] | None:
        """The paused call only if it has NOT been resolved yet (else ``None``)."""
        already_resolved = await self.conversation_repository.get_approval_decision(
            conversation_id=conversation_id,
            approval_id=approval_id,
        )
        if already_resolved is not None:
            return None
        return await self._paused_call_from_messages(
            conversation_id=conversation_id,
            approval_id=approval_id,
        )

    async def get_pending_ask_user(
        self,
        *,
        conversation_id: UUID,
    ) -> dict[str, object] | None:
        """Oldest unresolved ``ask_user`` pause for a conversation, or ``None``.

        Returns ``{tool_call_id, kind, tool_args, agent_run_id}``. Used by surface
        ingress to render the questions on the surface (from a WAITING event) and
        to route a typed reply back into the run as the answer.
        """
        return await self._oldest_unresolved_pause(
            conversation_id=conversation_id,
            tool_names=("ask_user",),
        )

    async def get_pending_user_interaction(
        self,
        *,
        conversation_id: UUID,
    ) -> dict[str, object] | None:
        """Oldest unresolved pausing tool call (``ask_user`` or
        ``request_approval``) for a conversation, or ``None``.

        Returns ``{tool_call_id, kind, tool_args, agent_run_id}``. Used by
        surface ingress to route a typed reply back into the paused run.
        """
        return await self._oldest_unresolved_pause(
            conversation_id=conversation_id,
            tool_names=_PAUSING_TOOL_NAMES,
        )

    async def _oldest_unresolved_pause(
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
                        message.tool_args
                        if isinstance(message.tool_args, dict)
                        else {}
                    ),
                    "agent_run_id": message.agent_run_id,
                }
        return None

    async def add_user_message_and_start_run(
        self,
        *,
        conversation_id: UUID | None,
        user_id: UUID,
        content: str,
        pod_id: UUID,
        agent_name: str | None = None,
        message_metadata: dict[str, object] | None = None,
        require_execute_grant: bool = True,
    ) -> AgentRunStartResult:
        conversation = await self._get_or_create_conversation_for_message(
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
            require_grant=require_execute_grant,
        )

        expected_agent_id = await self._expected_agent_id(
            pod_id=pod_id,
            agent_name=agent_name,
        )
        # _get_or_create_conversation_for_message already returned a fully loaded,
        # access-checked conversation — no need to re-fetch it.
        self._validate_conversation_access(
            conversation,
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
        )
        # require_execute_grant is False only for self-spawn (SubAgentService).
        if require_execute_grant:
            await self._require_agent_action(
                user_id=user_id,
                pod_id=pod_id,
                agent_id=conversation.agent_id,
                action=Permissions.AGENT_EXECUTE,
            )
        # Resolve the agent (a read) before taking the conversation lock, so the
        # FOR UPDATE span covers only the active-run check + run/message writes.
        agent = await self._resolve_agent(conversation=conversation, user_id=user_id)

        await self.conversation_repository.lock_conversation(conversation.id)
        active_run = await self.conversation_repository.get_active_agent_run_for_update(
            conversation.id
        )
        started_new_run = active_run is None
        superseded_returns: list[Message] = []
        if active_run is None:
            # A prior run may have paused on ask_user/request_approval (conversation
            # -> WAITING) without the user ever resolving it — the composer stays
            # enabled during WAITING, so the user can type past the card. Deny any
            # such leftover call now: otherwise this new run's history rebuild finds
            # no matching return for it and silently drops it (see
            # PydanticAIHarness._build_tool_batch), permanently losing the model's
            # memory of asking and leaving the UI card stuck "needs your input".
            superseded_returns = await self._supersede_stale_pending_interactions(
                conversation=conversation,
                user_id=user_id,
            )
            selected_agent_runtime = (
                conversation.agent_runtime
                or agent.agent_runtime
                or await self._default_agent_runtime_for_pod(
                    pod_id=conversation.pod_id
                )
            )
            await self._assert_usage_preflight_allowed(
                organization_id=conversation.organization_id,
                user_id=user_id,
                agent_runtime=selected_agent_runtime,
            )
            active_run = await self.conversation_repository.create_agent_run(
                conversation_id=conversation.id,
                agent_id=conversation.agent_id,
                agent_runtime=selected_agent_runtime,
                metadata={"source": "user_message"},
            )

        metadata = {
            "during_active_run": not started_new_run,
            **(message_metadata or {}),
        }
        metadata.pop("author_user_id", None)
        metadata.pop("agent_run_id", None)

        saved_user_message = await self.conversation_repository.append_message(
            conversation_id=conversation.id,
            agent_run_id=active_run.id,
            draft=MessageDraft.of_text(
                content,
                role=MessageRole.USER,
                metadata=metadata,
            ),
        )

        if started_new_run and not _SUPPRESS_RUN_ENQUEUE.get():
            self.uow.collect_events(
                [
                    AgentRunStartedEvent(
                        conversation_id=conversation.id,
                        agent_run_id=active_run.id,
                        user_id=user_id,
                        pod_id=pod_id,
                        agent_name=agent_name,
                    )
                ]
            )

        # Streaming endpoints need the message/run and its outbox event committed
        # atomically before the worker can safely load them; normal CRUD methods
        # still rely on the request UoW.
        await self.uow.commit()
        # Publish superseded-interaction returns only now that they're durably
        # committed alongside the new run/message (same transaction as above).
        for superseded_return in superseded_returns:
            await publish_conversation_event(
                conversation.id,
                message_payload(
                    superseded_return.agent_run_id,
                    message_to_payload(superseded_return),
                ),
            )
        await publish_conversation_event(
            conversation.id,
            input_added_payload(active_run.id, message_to_payload(saved_user_message)),
        )
        return AgentRunStartResult(
            conversation_id=conversation.id,
            agent_run_id=active_run.id,
            started_new_run=started_new_run,
        )

    async def stop_conversation(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
    ) -> Conversation:
        expected_agent_id = await self._expected_agent_id(
            pod_id=pod_id,
            agent_name=agent_name,
        )
        conversation = await self.conversation_repository.get_conversation(
            conversation_id
        )
        self._validate_conversation_access(
            conversation,
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
        )
        await self._require_agent_action(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=conversation.agent_id,
            action=Permissions.AGENT_EXECUTE,
        )
        active_run = await self.conversation_repository.get_active_agent_run_for_update(
            conversation.id
        )
        if active_run is not None:
            finish_result = await self.conversation_repository.finish_agent_run(
                agent_run_id=active_run.id,
                status=AgentRunStatus.STOP_REQUESTED,
            )
            if finish_result is not None:
                conversation.status = finish_result.conversation_status
            self.conversation_repository.collect_events(
                [
                    AgentRunStopRequestedEvent(
                        conversation_id=conversation.id,
                        agent_run_id=active_run.id,
                        user_id=user_id,
                    )
                ]
            )
            await self.uow.commit()
            return conversation

        # No active run, but the conversation may still be suspended. A snoozed
        # turn has *no* run by construction — it ended cleanly when the tool
        # paused it — so without this, Stop silently did nothing and the timer
        # still fired later.
        await self._cancel_active_snooze(conversation=conversation)
        return conversation

    @property
    def wait_repository(self) -> AgentConversationWaitRepository:
        # Built on demand rather than in __init__: the repository binds a session
        # eagerly, and plenty of callers construct this service without a real
        # unit of work to exercise paths that never touch the database.
        return AgentConversationWaitRepository(self.uow)

    async def _cancel_active_snooze(self, *, conversation: Conversation) -> None:
        """Stop a sleeping agent for good: drop the timer, never resume.

        The CANCELLED tool return is still written, so the paused call is not
        left dangling in history — a tool call with no return is dropped when
        history is rebuilt, and the model would see a turn that ends mid-thought.
        What is deliberately skipped is ``start_resume_run_if_ready``: Stop means
        the agent does not wake.
        """
        wait = await self.wait_repository.find_active_for_conversation(conversation.id)
        if wait is None:
            return

        wait.cancel()
        await self.wait_repository.update(wait)
        await self.conversation_repository.set_conversation_status(
            conversation_id=conversation.id,
            status=ConversationStatus.STOPPED,
        )
        conversation.status = ConversationStatus.STOPPED
        await self.append_pause_tool_return(
            conversation=conversation,
            paused_run_id=wait.agent_run_id,
            tool_call_id=wait.tool_call_id,
            tool_name="snooze",
            tool_result=build_snooze_result(
                woke_because="CANCELLED",
                slept_seconds=elapsed_seconds((wait.spec or {}).get("started_at")),
                note_to_self=(wait.spec or {}).get("note_to_self"),
            ),
        )
        if wait.external_ref:
            await cancel_snooze_wake(wait.external_ref)

    async def _get_or_create_conversation_for_message(
        self,
        *,
        conversation_id: UUID | None,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None,
        require_grant: bool = True,
    ) -> Conversation:
        if conversation_id is not None:
            return await self.get_conversation(
                conversation_id=conversation_id,
                user_id=user_id,
                pod_id=pod_id,
                agent_name=agent_name,
                require_read_grant=require_grant,
            )
        return await self.create_conversation(
            pod_id=pod_id,
            agent_name=agent_name,
            user_id=user_id,
        )

    async def _resolve_agent(
        self,
        *,
        conversation: Conversation,
        user_id: UUID,
    ) -> Agent:
        if conversation.agent_id is None:
            # Lazy import: registry imports the subagents toolset, which imports
            # this service — importing it at module load would cycle.
            from app.modules.agent.tools.registry import POD_DEFAULT_AGENT_TOOLSETS

            return Agent(
                id=_POD_ASSISTANT_AGENT_ID,
                pod_id=conversation.pod_id,
                user_id=user_id,
                name=DEFAULT_POD_AGENT_NAME,
                instruction="",
                agent_runtime=conversation.agent_runtime,
                toolsets=list(POD_DEFAULT_AGENT_TOOLSETS),
            )
        agent = await self.agent_repository.get(conversation.agent_id)
        if agent is None:
            raise AgentNotFoundError(str(conversation.agent_id))
        return agent

    async def _resolve_agent_for_path(
        self,
        *,
        pod_id: UUID,
        agent_name: str,
    ) -> Agent:
        agent = await self.agent_repository.get_by_pod_and_name(
            pod_id=pod_id,
            name=agent_name,
        )
        if agent is None:
            raise AgentNotFoundError(agent_name)
        return agent

    async def _expected_agent_id(
        self,
        *,
        pod_id: UUID,
        agent_name: str | None,
    ) -> UUID | None:
        if agent_name is None:
            return None
        agent = await self._resolve_agent_for_path(
            pod_id=pod_id,
            agent_name=agent_name,
        )
        return agent.id

    async def _get_pod_organization_id(self, pod_id: UUID) -> UUID | None:
        return await create_agent_pod_repository(self.uow).get_organization_id(pod_id)

    async def _default_agent_runtime_for_pod(
        self,
        *,
        pod_id: UUID,
    ) -> AgentRuntimeConfig:
        config = await create_agent_pod_repository(self.uow).get_config(pod_id)
        runtime = PodConfig.from_raw(config).resolved_default_runtime()
        return runtime or AgentRuntimeConfig(
            profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID
        )

    async def _assert_usage_preflight_allowed(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID,
        agent_runtime: AgentRuntimeConfig,
    ) -> None:
        if self.usage_service is None:
            return
        if not agent_runtime.profile_id.startswith("system:"):
            return
        limits = await self.usage_service.get_usage_limits(
            organization_id=organization_id,
            user_id=user_id,
        )
        if limits["allowed"]:
            return
        raise UsageLimitExceededError()

    async def _authorized_conversation(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None,
        action: str,
    ) -> Conversation:
        expected_agent_id = await self._expected_agent_id(
            pod_id=pod_id,
            agent_name=agent_name,
        )
        conversation = await self.conversation_repository.get_conversation(
            conversation_id
        )
        self._validate_conversation_access(
            conversation,
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
        )
        await self._require_agent_action(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=conversation.agent_id,
            action=action,
        )
        return conversation

    async def _require_agent_action(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        agent_id: UUID | None,
        action: str,
    ) -> None:
        await self._require_agent_actions(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=agent_id,
            actions=(action,),
        )

    async def _require_agent_actions(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        agent_id: UUID | None,
        actions: Sequence[str],
    ) -> None:
        _ = user_id
        ctx = get_current_context()
        if ctx is None:
            raise RuntimeError("Context is required for conversation authorization")
        resource = ResourceRef(
            resource_type=ResourceType.AGENT
            if agent_id is not None
            else ResourceType.POD,
            resource_id=agent_id or pod_id,
            pod_id=pod_id,
        )
        await ctx.require_all([(action, resource) for action in actions])

    def _validate_conversation_access(
        self,
        conversation: Conversation | None,
        *,
        user_id: UUID,
        pod_id: UUID,
        agent_id: UUID | None,
    ) -> None:
        if conversation is None:
            raise ConversationNotFoundError()
        if conversation.user_id != user_id:
            raise ConversationNotFoundError()
        if conversation.pod_id != pod_id:
            raise ConversationNotFoundError()
        if agent_id is not None and conversation.agent_id != agent_id:
            raise ConversationNotFoundError()
