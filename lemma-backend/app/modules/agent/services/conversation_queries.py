"""Reading a conversation, and what the reader is allowed to see.

Every one of these is the same three steps -- resolve which agent the caller
means, load the row, check the caller may have it -- followed by the read
itself. They are together because that preamble is the interesting part: the
authorization is per-agent, not per-pod, so "list the conversations in this pod"
and "list this agent's conversations" are different questions with different
answers, and getting that boundary wrong is how one user sees another's thread.

Writes live in `conversation_turns`; this side never starts a run.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.entities import AgentRun, Conversation, Message
from app.modules.agent.domain.ports import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationAgentSelection,
    ConversationStatus,
    ConversationType,
)
from app.modules.agent.services.approval_reconciliation import (
    pending_user_approval_messages,
)
from app.modules.agent.services.conversation_access import (
    authorized_conversation,
    require_agent_action,
    resolve_expected_agent_id,
    validate_conversation_access,
)


class ConversationQueries:
    """The read half of the conversation service."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        conversation_repository: ConversationRepository,
        agent_repository: AgentRepository,
    ) -> None:
        self.uow = uow
        self.conversation_repository = conversation_repository
        self.agent_repository = agent_repository

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
        archived: bool = False,
        cursor: UUID | None = None,
        limit: int = 20,
    ) -> tuple[list[Conversation], UUID | None]:
        expected_agent_id = await resolve_expected_agent_id(
            self.agent_repository,
            pod_id=pod_id,
            agent_name=agent_selection.value,
        )
        resolved_selection = agent_selection.resolve(expected_agent_id)
        await require_agent_action(
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
            archived=archived,
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
        expected_agent_id = await resolve_expected_agent_id(
            self.agent_repository,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        # The latest run carries the failure diagnostics and the retry decision.
        conversation = validate_conversation_access(
            await self.conversation_repository.get_conversation(
                conversation_id,
                include_runs=True,
            ),
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
        )
        # require_read_grant is False only for self-spawn (an agent operating on
        # its own conversation tree): ownership is validated above, but the agent
        # holds no agent.read grant on itself, so skip the cross-agent grant check.
        if require_read_grant:
            await require_agent_action(
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
        return await self.conversation_repository.run_has_only_user_messages(latest.id)

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
        expected_agent_id = await resolve_expected_agent_id(
            self.agent_repository,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        conversation = validate_conversation_access(
            await self.conversation_repository.get_conversation(conversation_id),
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
        )
        await require_agent_action(
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
        expected_agent_id = await resolve_expected_agent_id(
            self.agent_repository,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        conversation = validate_conversation_access(
            await self.conversation_repository.get_conversation(conversation_id),
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
        )
        await require_agent_action(
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
        conversation = await authorized_conversation(
            self.conversation_repository,
            self.agent_repository,
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
