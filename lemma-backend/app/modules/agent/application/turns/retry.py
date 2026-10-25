"""Retrying a conversation's failed run.

A fresh run in place of the failed latest one, from the conversation's
persisted history and without a duplicate user message. It passes the same
runtime and usage checks as any other turn.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.modules.agent.domain.run_state import RunState
from app.modules.agent.domain.errors import ConversationStateError
from app.modules.agent.contracts.turns import stage_agent_turn, turn_for
from app.modules.agent.domain.value_objects import (
    AgentRunStartResult,
    AgentRunStatus,
    RunTrigger,
)
from app.modules.agent.services.conversation_access import (
    authorized_conversation,
    resolve_agent,
)
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.services.runtime_availability import (
    TurnRuntimeCheck,
    ensure_turn_runtime_available,
    turn_runtime,
)
from app.modules.agent.services.usage_preflight import assert_turn_allowed
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.ports import AgentRepository
from app.modules.agent.infrastructure import repositories
from app.modules.usage.contracts.execution import UsageService, build_usage_service


class RetryFailedRun:
    """Starts a fresh run in place of a conversation's failed latest one."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        agent_repository: AgentRepository,
        usage_service: UsageService | None,
        runtime_check: TurnRuntimeCheck = ensure_turn_runtime_available,
    ) -> None:
        self.uow = uow
        self.conversations = Conversations(uow)
        self.runs = Runs(uow)
        self.agent_repository = agent_repository
        self.usage_service = usage_service
        self.runtime_check = runtime_check

    @classmethod
    def build(cls, uow: SqlAlchemyUnitOfWork) -> RetryFailedRun:
        return cls(
            uow,
            agent_repository=repositories.AgentRepository(uow),
            usage_service=build_usage_service(uow),
        )

    async def retry(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
    ) -> AgentRunStartResult:
        conversation = await authorized_conversation(
            self.conversations,
            self.agent_repository,
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
            action=Permissions.AGENT_EXECUTE,
        )
        await self.conversations.lock_conversation(conversation.id)
        active_run = await self.runs.get_active_agent_run_for_update(conversation.id)
        if active_run is not None:
            if active_run.state.trigger is RunTrigger.RETRY:
                return AgentRunStartResult(
                    conversation_id=conversation.id,
                    agent_run_id=active_run.id,
                    started_new_run=False,
                )
            raise ConversationStateError("Conversation already has an active run")

        failed_run = await self.runs.get_latest_agent_run_for_conversation(
            conversation.id
        )
        if failed_run is None or failed_run.status != AgentRunStatus.FAILED:
            raise ConversationStateError("The latest conversation run did not fail")
        # Asks about this one run rather than loading every run of the
        # conversation with every message attached to find it again -- the run
        # is already in hand, and only its message roles were ever in question.
        if not await self.runs.run_is_replay_safe(failed_run.id):
            raise ConversationStateError("The failed run cannot be retried safely")

        # The runtime the conversation is set to now, not the failed run's: a
        # run that died on a retired model would die the same way again, and
        # the usual fix is to re-point the teammate at another model first.
        runtime = await turn_runtime(
            self.uow,
            conversation,
            await resolve_agent(
                conversation, user_id=user_id, agent_repository=self.agent_repository
            ),
        )
        await self.runtime_check(
            self.uow,
            runtime,
            organization_id=conversation.organization_id,
            user_id=user_id,
        )
        await assert_turn_allowed(
            self.usage_service, conversation, user_id=user_id, agent_runtime=runtime
        )
        retry_run = await self.runs.create_agent_run(
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            agent_runtime=runtime,
            state=RunState(
                trigger=RunTrigger.RETRY,
                retried_run_id=failed_run.id,
                **failed_run.state.privacy.model_dump(),
            ),
        )
        await stage_agent_turn(
            self.uow,
            turn_for(
                conversation,
                run_id=retry_run.id,
                user_id=user_id,
                agent_name=agent_name,
            ),
        )
        await self.uow.commit()
        return AgentRunStartResult(
            conversation_id=conversation.id,
            agent_run_id=retry_run.id,
            started_new_run=True,
        )
