"""Retrying a conversation's failed run.

A collaborator of ``ConversationService`` like ``TurnCoordinator``, not a
subclass of it: it needs the repositories and the turn's runtime and usage
checks, and nothing else the service carries.
"""

from __future__ import annotations

from app.modules.agent.domain.conversation_metadata import (
    MANUAL_RETRY_SOURCE,
    SOURCE_KEY,
    source_of,
)
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.modules.agent.domain.private_notes import privacy_of
from app.modules.agent.domain.errors import ConversationStateError
from app.modules.agent.contracts.turns import stage_agent_turn, turn_for
from app.modules.agent.domain.value_objects import (
    AgentRunStartResult,
    AgentRunStatus,
)
from app.modules.agent.services.conversation_access import (
    authorized_conversation,
    resolve_agent,
)
from app.modules.agent.services.runtime_availability import turn_runtime

if TYPE_CHECKING:
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.agent.domain.ports import (
        AgentRepository,
        ConversationRepository,
    )
    from app.modules.agent.services.conversation_turns import TurnCoordinator


class RetryCoordinator:
    """Starts a fresh run in place of a conversation's failed latest one."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        conversation_repository: ConversationRepository,
        agent_repository: AgentRepository,
        turns: TurnCoordinator,
    ) -> None:
        self.uow = uow
        self.conversation_repository = conversation_repository
        self.agent_repository = agent_repository
        self.turns = turns

    async def retry_failed_run(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
    ) -> AgentRunStartResult:
        conversation = await authorized_conversation(
            self.conversation_repository,
            self.agent_repository,
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
            if source_of(active_run.metadata) == MANUAL_RETRY_SOURCE:
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
        # Asks about this one run rather than loading every run of the
        # conversation with every message attached to find it again -- the run
        # is already in hand, and only its message roles were ever in question.
        if not await self.conversation_repository.run_is_replay_safe(failed_run.id):
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
        await self.turns.runtime_check(
            self.uow,
            runtime,
            organization_id=conversation.organization_id,
            user_id=user_id,
        )
        await self.turns.assert_usage_preflight_allowed(
            organization_id=conversation.organization_id,
            user_id=user_id,
            agent_runtime=runtime,
        )
        retry_run = await self.conversation_repository.create_agent_run(
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            agent_runtime=runtime,
            metadata={
                SOURCE_KEY: MANUAL_RETRY_SOURCE,
                "retried_agent_run_id": str(failed_run.id),
                **privacy_of(failed_run.metadata),
            },
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
