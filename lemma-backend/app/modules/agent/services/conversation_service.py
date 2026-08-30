"""Conversation service for unified agent chats."""

from __future__ import annotations

from uuid import UUID


from app.modules.agent.services.conversation_approvals import (
    ApprovalCoordinator,
)
from app.modules.agent.services.conversation_queries import ConversationQueries
from app.modules.agent.services.conversation_turns import TurnCoordinator
from app.modules.agent.services.conversation_resume_return import (
    ResumeToolReturnBuilder,
)
from app.modules.agent.domain.sentinels import UNSET, UnsetType
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.services.conversation_access import (
    authorized_conversation,
    resolve_expected_agent_id,
    require_agent_action,
    require_agent_actions,
    resolve_agent_for_path,
    validate_conversation_access,
)
from app.modules.agent.domain.conversation_titles import (
    normalize_conversation_title,
)
from app.modules.agent.domain.entities import (
    Conversation,
)
from app.modules.agent.domain.errors import (
    ConversationNotFoundError,
)
from app.modules.agent.domain.ports import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.domain.approvals import ApprovalResolution
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    AgentRunStartResult,
    AgentRuntimeConfig,
    ConversationType,
)
from app.modules.agent.services.workspace_location import (
    apply_location_metadata,
)
from app.composition.agent_pod import create_agent_pod_repository
from app.composition.agent_usage import UsageService
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.pause_resume import (
    PAUSING_TOOL_NAMES,
    PauseResume,
)


# Defined with the primitive that consumes it, so the list and the resume path
# that depends on it cannot drift apart.
_PAUSING_TOOL_NAMES = PAUSING_TOOL_NAMES


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
        self.resume_returns = ResumeToolReturnBuilder(uow, agent_repository)
        self.pauses = PauseResume(uow, conversation_repository, agent_repository)
        self.approvals = ApprovalCoordinator(
            uow, conversation_repository, self.resume_returns, self.pauses
        )
        self.queries = ConversationQueries(
            uow, conversation_repository, agent_repository
        )
        self.turns = TurnCoordinator(
            uow,
            conversation_repository,
            agent_repository,
            self.approvals,
            self.pauses,
            usage_service,
        )

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
            await resolve_agent_for_path(
                self.agent_repository, pod_id=pod_id, agent_name=agent_name
            )
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
            await require_agent_actions(
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

    async def update_conversation(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
        title: str | None | UnsetType = UNSET,
        instructions: str | None | UnsetType = UNSET,
        agent_runtime: AgentRuntimeConfig | None | UnsetType = UNSET,
        metadata: dict[str, object] | None | UnsetType = UNSET,
        is_archived: bool | UnsetType = UNSET,
    ) -> Conversation:
        expected_agent_id = await resolve_expected_agent_id(
            self.agent_repository,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        conversation = await self.conversation_repository.get_conversation(
            conversation_id
        )
        validate_conversation_access(
            conversation,
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
        )
        if conversation is None:
            raise ConversationNotFoundError()
        await require_agent_action(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=conversation.agent_id,
            action=Permissions.AGENT_EXECUTE,
        )

        if not isinstance(title, UnsetType):
            # A blank title normalizes to None, which is not "no change" -- it
            # is the conversation asking to be auto-titled again, since
            # `generate_title_if_absent` fires on a null title.
            conversation.title = normalize_conversation_title(title)
        if not isinstance(instructions, UnsetType):
            conversation.instructions = instructions
        if not isinstance(agent_runtime, UnsetType):
            conversation.agent_runtime = agent_runtime
        if not isinstance(metadata, UnsetType):
            conversation.metadata = metadata
        if not isinstance(is_archived, UnsetType):
            conversation.is_archived = is_archived

        return await self.conversation_repository.update_conversation(conversation)

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
        conversation = await authorized_conversation(
            self.conversation_repository,
            self.agent_repository,
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

        Kept on the service because `agent_surfaces` reaches this and
        `get_pending_user_interaction` off one import of this class; see
        `ApprovalCoordinator` for what it does.
        """
        return await self.approvals.resolve_user_approval_internal(
            conversation=conversation,
            approval_id=approval_id,
            user_id=user_id,
            pod_id=pod_id,
            decision=decision,
            response=response,
            agent_name=agent_name,
            defer_reconciliation=defer_reconciliation,
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
        return await self.approvals.oldest_unresolved_pause(
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
        return await self.approvals.oldest_unresolved_pause(
            conversation_id=conversation_id,
            tool_names=_PAUSING_TOOL_NAMES,
        )

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

        expected_agent_id = await resolve_expected_agent_id(
            self.agent_repository,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        # _get_or_create_conversation_for_message already returned a fully loaded,
        # access-checked conversation — no need to re-fetch it.
        validate_conversation_access(
            conversation,
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
        )
        # require_execute_grant is False only for self-spawn (SubAgentService).
        if require_execute_grant:
            await require_agent_action(
                user_id=user_id,
                pod_id=pod_id,
                agent_id=conversation.agent_id,
                action=Permissions.AGENT_EXECUTE,
            )
        return await self.turns.start(
            conversation,
            user_id=user_id,
            pod_id=pod_id,
            content=content,
            agent_name=agent_name,
            message_metadata=message_metadata,
        )

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
            return await self.queries.get_conversation(
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

    async def _get_pod_organization_id(self, pod_id: UUID) -> UUID | None:
        return await create_agent_pod_repository(self.uow).get_organization_id(pod_id)

    async def stop_conversation(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
    ) -> Conversation:
        """Stop the run in flight, closing whatever pause it was sitting on."""
        return await self.turns.stop_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
        )

    @property
    def wait_repository(self) -> AgentConversationWaitRepository:
        """The snooze timer store, reached through the turn coordinator."""
        return self.turns.wait_repository
