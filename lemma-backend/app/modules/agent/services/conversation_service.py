"""Conversation service for unified agent chats."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID


from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.current import get_current_context
from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_ID,
    DEFAULT_POD_AGENT_NAME,
)
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.events.publisher import EventPublisher
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
)
from app.modules.agent.domain.events import (
    AGENT_EVENTS_STREAM,
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
    ConversationStatus,
    ConversationType,
    MessageDraft,
    MessageKind,
    MessageRole,
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
from app.modules.agent.services.workspace_location import resolve_workspace_location
from app.modules.pod.infrastructure.pod_repositories import PodRepository
from app.modules.usage.services.usage_service import UsageService

_POD_ASSISTANT_AGENT_ID = DEFAULT_POD_AGENT_ID

# Tools that pause the run for user input. Both persist their tool call (rendered
# as a card by the client) and are resolved via the approvals endpoint, which
# synthesizes their tool return and resumes the run.
_PAUSING_TOOL_NAMES = ("ask_user", "request_approval")


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


class ConversationService:
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
            # Dispatching a *named* agent needs both agent.execute (to start it)
            # and agent.read (to load the conversation back when starting the
            # run). Check them together so a caller missing a grant sees every
            # missing permission at once instead of fixing one, retrying, and
            # hitting the next 403. The downstream read check reuses the cached
            # decision. The default pod agent (agent is None) needs only execute.
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
        """Always record the conversation's workspace cwd in metadata.

        A child (``parent_id`` set — a sub-agent OR a conversation pinned under a
        PROJECT) inherits the parent's resolved cwd + workspace selection, so it
        shares the parent's directory instead of getting its own. A root
        conversation gets its own ``/workspace/conversations/{id}`` cwd. An
        explicit ``cwd`` already in metadata always wins.
        """
        metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
        if metadata.get("cwd"):
            return
        parent = (
            await self.conversation_repository.get_conversation(parent_id)
            if parent_id is not None
            else None
        )
        if parent is not None:
            parent_meta = parent.metadata if isinstance(parent.metadata, dict) else {}
            metadata["cwd"] = resolve_workspace_location(parent).cwd
            for key in ("workspace", "workspace_id", "workspace_name"):
                if key in parent_meta:
                    metadata.setdefault(key, parent_meta[key])
        else:
            metadata["cwd"] = resolve_workspace_location(conversation).cwd
        conversation.metadata = metadata

    async def list_conversations(
        self,
        *,
        pod_id: UUID,
        agent_name: str | None,
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
            agent_name=agent_name,
        )
        await self._require_agent_action(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
            action=Permissions.AGENT_READ,
        )
        return await self.conversation_repository.list_conversations(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
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
        # include_runs so the response carries last_run_status/error — a single
        # `conversations get` can explain why a run FAILED.
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
        return conversation

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
        resolved_ids = await self.conversation_repository.list_resolved_approval_ids(
            conversation_id=conversation.id
        )
        messages, _ = await self.conversation_repository.list_messages(
            conversation_id=conversation.id,
            limit=500,
        )
        return [
            message
            for message in messages
            if message.kind == MessageKind.TOOL_CALL
            and message.tool_name in _PAUSING_TOOL_NAMES
            and message.tool_call_id not in resolved_ids
        ]

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
    ) -> None:
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
        await self.resolve_user_approval_internal(
            conversation=conversation,
            approval_id=approval_id,
            user_id=user_id,
            pod_id=pod_id,
            decision=decision,
            response=response,
            agent_name=agent_name,
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
    ) -> None:
        """Resume a paused run for an already-authorized + loaded conversation.

        Carries the safety-critical core of :meth:`resolve_user_approval` (the
        unique decision-record lock, the synthesized tool return, the
        all-siblings-resolved resume gate). Callers MUST have authorized the
        resolver against this conversation first. The caller's current auth
        context must be the conversation owner's, since an approved
        ``request_approval`` runs the wrapped tool with that authority.
        """
        pending = await self._pending_user_approval_from_messages(
            conversation_id=conversation.id,
            approval_id=approval_id,
        )
        if pending is None:
            raise RuntimeError("Approval is not pending or no longer live")
        kind = str(pending["kind"])
        tool_args = pending["tool_args"] if isinstance(pending["tool_args"], dict) else {}

        # Record the decision first so the unique (conversation, approval) row locks
        # out a concurrent double-submit before any side-effecting tool runs.
        decision_tool_name = (
            "ask_user"
            if kind == "ask_user"
            else str(tool_args.get("tool_name") or "request_approval")
        )
        recorded = await self.conversation_repository.record_approval_decision(
            conversation_id=conversation.id,
            approval_id=approval_id,
            agent_run_id=pending["agent_run_id"],
            tool_name=decision_tool_name,
            decision=decision,
            response=response or {},
            resolved_by_user_id=user_id,
        )
        if not recorded:
            raise RuntimeError("Approval already resolved")
        await self.uow.commit()

        # Synthesize the tool return the resumed run will replay, and persist it under
        # the *paused* run (the one that made the call). For an approved
        # request_approval this runs the wrapped tool as the user. History is
        # reconstructed per conversation, so _build_tool_batch pairs this return with
        # its call regardless of which run each lives in.
        paused_run_id = pending["agent_run_id"]
        return_tool_name, tool_result = await self._build_resume_tool_return(
            conversation=conversation,
            user_id=user_id,
            kind=kind,
            tool_args=tool_args,
            decision=decision,
            response=response or {},
            paused_agent_run_id=paused_run_id,
        )
        saved_return = await self.conversation_repository.append_message(
            conversation_id=conversation.id,
            agent_run_id=paused_run_id,
            draft=MessageDraft.of_tool_return(
                tool_call_id=approval_id,
                tool_name=return_tool_name,
                tool_result=tool_result,
            ),
        )
        await self.uow.commit()
        await publish_conversation_event(
            conversation.id,
            message_payload(paused_run_id, message_to_payload(saved_return)),
        )

        # A turn can pause with several pending interactions (e.g. request_approval +
        # ask_user in one assistant turn). Resume only once every pausing tool call in
        # the paused run is resolved — otherwise the unresolved sibling would be
        # orphaned (no return) and dropped from the resumed run's history, making the
        # agent re-ask it. The conversation lock serializes the resume decision so two
        # near-simultaneous resolves don't each start a run.
        await self.conversation_repository.lock_conversation(conversation.id)
        remaining = await self._unresolved_pausing_call_ids(
            conversation_id=conversation.id,
            agent_run_id=paused_run_id,
        )
        if remaining:
            # Still waiting on the user; the frontend reload surfaces the next card.
            await self.uow.commit()
            return
        active_run = await self.conversation_repository.get_active_agent_run_for_update(
            conversation.id
        )
        if active_run is not None:
            # Another resolve already started the resume run (or a normal run is live);
            # it will replay the now-complete tool returns. Nothing more to do.
            await self.uow.commit()
            return
        agent = await self._resolve_agent(conversation=conversation, user_id=user_id)
        selected_agent_runtime = (
            conversation.agent_runtime
            or agent.agent_runtime
            or await self._default_agent_runtime_for_pod(pod_id=conversation.pod_id)
        )
        resume_run = await self.conversation_repository.create_agent_run(
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            agent_runtime=selected_agent_runtime,
            metadata={"source": "approval_resume", "resumed_tool_call_id": approval_id},
        )
        await self.uow.commit()
        await EventPublisher.publish(
            AGENT_EVENTS_STREAM,
            AgentRunStartedEvent(
                conversation_id=conversation.id,
                agent_run_id=resume_run.id,
                user_id=user_id,
                pod_id=pod_id,
                agent_name=agent_name,
            ),
        )

    async def _unresolved_pausing_call_ids(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID,
    ) -> list[str]:
        """Pausing tool calls in the paused run that still lack a recorded decision."""
        resolved_ids = await self.conversation_repository.list_resolved_approval_ids(
            conversation_id=conversation_id
        )
        messages, _ = await self.conversation_repository.list_messages(
            conversation_id=conversation_id,
            limit=500,
        )
        return [
            message.tool_call_id
            for message in messages
            if message.kind == MessageKind.TOOL_CALL
            and message.tool_name in _PAUSING_TOOL_NAMES
            and message.agent_run_id == agent_run_id
            and message.tool_call_id is not None
            and message.tool_call_id not in resolved_ids
        ]

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
        from app.core.infrastructure.db.session import async_session_maker
        from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
        from app.modules.agent.domain.value_objects import to_json_value
        from app.modules.agent.tools.approval.executor import ApprovalExecutor

        try:
            deps = await self._build_resume_context(
                conversation=conversation,
                user_id=user_id,
                agent_run_id=agent_run_id,
            )
            executor = ApprovalExecutor(SessionUnitOfWorkFactory(async_session_maker))
            result = await executor.execute_as_user(
                deps=deps,
                tool_name=tool_name,
                args=args,
            )
            return {"ok": True, "value": to_json_value(result)}
        except Exception as exc:  # noqa: BLE001 - reported back to the model, not fatal
            return {"ok": False, "error": str(exc)}

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
        )

    async def _pending_user_approval_from_messages(
        self,
        *,
        conversation_id: UUID,
        approval_id: str,
    ) -> dict[str, object] | None:
        already_resolved = await self.conversation_repository.get_approval_decision(
            conversation_id=conversation_id,
            approval_id=approval_id,
        )
        if already_resolved is not None:
            return None
        messages, _ = await self.conversation_repository.list_messages(
            conversation_id=conversation_id,
            limit=500,
        )
        for message in messages:
            if (
                message.kind != MessageKind.TOOL_CALL
                or message.tool_name not in _PAUSING_TOOL_NAMES
                or message.tool_call_id != approval_id
                or message.agent_run_id is None
            ):
                continue
            tool_args = (
                message.tool_args if isinstance(message.tool_args, dict) else {}
            )
            return {
                "agent_run_id": message.agent_run_id,
                "kind": message.tool_name,
                "tool_args": tool_args,
            }
        return None

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
                and message.tool_name == "ask_user"
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
                and message.tool_name in _PAUSING_TOOL_NAMES
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
        conversation = await self.conversation_repository.get_conversation(
            conversation.id
        )
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
        await self.conversation_repository.lock_conversation(conversation.id)

        agent = await self._resolve_agent(conversation=conversation, user_id=user_id)
        active_run = await self.conversation_repository.get_active_agent_run_for_update(
            conversation.id
        )
        started_new_run = active_run is None
        if active_run is None:
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

        # Streaming endpoints need the message/run committed before the worker
        # can safely load them; normal CRUD methods still rely on request UoW.
        await self.uow.commit()
        await publish_conversation_event(
            conversation.id,
            input_added_payload(active_run.id, message_to_payload(saved_user_message)),
        )
        if started_new_run and not _SUPPRESS_RUN_ENQUEUE.get():
            await EventPublisher.publish(
                AGENT_EVENTS_STREAM,
                AgentRunStartedEvent(
                    conversation_id=conversation.id,
                    agent_run_id=active_run.id,
                    user_id=user_id,
                    pod_id=pod_id,
                    agent_name=agent_name,
                ),
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
        return await PodRepository(self.uow).get_organization_id(pod_id)

    async def _default_agent_runtime_for_pod(
        self,
        *,
        pod_id: UUID,
    ) -> AgentRuntimeConfig:
        config = await PodRepository(self.uow).get_config(pod_id)
        profile_id = config.get("default_profile_id")
        if not isinstance(profile_id, str) or not profile_id.strip():
            profile_id = DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID
        return AgentRuntimeConfig(profile_id=profile_id)

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
        from app.modules.usage.domain.errors import UsageLimitExceededError

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
