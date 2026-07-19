"""Retry orchestration for failed conversation runs."""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.modules.agent.domain.errors import ConversationStateError
from app.modules.agent.domain.events import AgentRunStartedEvent
from app.modules.agent.domain.value_objects import (
    AgentRunStartResult,
    AgentRunStatus,
)
from app.modules.agent.services.conversation_service import ConversationService


class ConversationRetryService(ConversationService):
    """Conversation service extension for explicit failed-run retries."""

    async def retry_failed_run(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
    ) -> AgentRunStartResult:
        conversation = await self._authorized_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
            action=Permissions.AGENT_EXECUTE,
        )
        await self.conversation_repository.lock_conversation(conversation.id)
        active_run = await self.conversation_repository.get_active_agent_run_for_update(
            conversation.id
        )
        if active_run is not None:
            active_metadata = active_run.metadata or {}
            if active_metadata.get("source") == "manual_retry":
                return AgentRunStartResult(
                    conversation_id=conversation.id,
                    agent_run_id=active_run.id,
                    started_new_run=False,
                )
            raise ConversationStateError("Conversation already has an active run")

        failed_run = (
            await self.conversation_repository.get_latest_agent_run_for_conversation(
                conversation.id
            )
        )
        if failed_run is None or failed_run.status != AgentRunStatus.FAILED:
            raise ConversationStateError("The latest conversation run did not fail")
        runs = (
            await self.conversation_repository.list_agent_runs_with_messages_by_run_id(
                failed_run.id
            )
        )
        persisted_failed_run = next(
            (run for run in runs if run.id == failed_run.id), None
        )
        if persisted_failed_run is None or not persisted_failed_run.is_safely_retryable:
            raise ConversationStateError("The failed run cannot be retried safely")

        await self._assert_usage_preflight_allowed(
            organization_id=conversation.organization_id,
            user_id=user_id,
            agent_runtime=failed_run.agent_runtime,
        )
        retry_run = await self.conversation_repository.create_agent_run(
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            agent_runtime=failed_run.agent_runtime,
            metadata={
                "source": "manual_retry",
                "retried_agent_run_id": str(failed_run.id),
            },
        )
        self.uow.collect_events(
            [
                AgentRunStartedEvent(
                    conversation_id=conversation.id,
                    agent_run_id=retry_run.id,
                    user_id=user_id,
                    pod_id=pod_id,
                    agent_name=agent_name,
                )
            ]
        )
        await self.uow.commit()
        return AgentRunStartResult(
            conversation_id=conversation.id,
            agent_run_id=retry_run.id,
            started_new_run=True,
        )
