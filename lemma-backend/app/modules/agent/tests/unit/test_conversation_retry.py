from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.agent.domain.entities import AgentRun, Conversation
from app.modules.agent.domain.errors import ConversationStateError
from app.modules.agent.domain.value_objects import AgentRunStatus, AgentRuntimeConfig
from app.modules.agent.services.conversation_service import ConversationService


def _run(*, status: AgentRunStatus) -> AgentRun:
    return AgentRun(
        conversation_id=uuid4(),
        status=status,
        agent_runtime=AgentRuntimeConfig(
            profile_id="user:daemon",
            model_name="claude-sonnet-4-5",
        ),
        started_at=datetime.now(timezone.utc),
    )


def _service():
    repository = SimpleNamespace(
        lock_conversation=AsyncMock(),
        get_active_agent_run_for_update=AsyncMock(return_value=None),
        get_latest_agent_run_for_conversation=AsyncMock(),
        create_agent_run=AsyncMock(),
    )
    uow = SimpleNamespace(collect_events=MagicMock(), commit=AsyncMock())
    service = ConversationService(
        uow=uow,
        conversation_repository=repository,
        agent_repository=SimpleNamespace(),
        authorization_service=SimpleNamespace(),
    )
    return service, repository, uow


@pytest.mark.asyncio
async def test_retry_failed_run_reuses_runtime_without_appending_message() -> None:
    service, repository, uow = _service()
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
    )
    failed_run = _run(status=AgentRunStatus.FAILED)
    failed_run.conversation_id = conversation.id
    retry_run = _run(status=AgentRunStatus.RUNNING)
    retry_run.conversation_id = conversation.id
    repository.get_latest_agent_run_for_conversation.return_value = failed_run
    repository.create_agent_run.return_value = retry_run
    service._authorized_conversation = AsyncMock(return_value=conversation)
    service._assert_usage_preflight_allowed = AsyncMock()

    result = await service.retry_failed_run(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
    )

    assert result.agent_run_id == retry_run.id
    repository.create_agent_run.assert_awaited_once_with(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        agent_runtime=failed_run.agent_runtime,
        metadata={
            "source": "manual_retry",
            "retried_agent_run_id": str(failed_run.id),
        },
    )
    uow.collect_events.assert_called_once()
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_failed_run_rejects_non_failed_latest_run() -> None:
    service, repository, _ = _service()
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())
    repository.get_latest_agent_run_for_conversation.return_value = _run(
        status=AgentRunStatus.COMPLETED
    )
    service._authorized_conversation = AsyncMock(return_value=conversation)

    with pytest.raises(ConversationStateError, match="did not fail"):
        await service.retry_failed_run(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
        )

    repository.create_agent_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_failed_run_rejects_an_active_run() -> None:
    service, repository, _ = _service()
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())
    repository.get_active_agent_run_for_update.return_value = _run(
        status=AgentRunStatus.RUNNING
    )
    service._authorized_conversation = AsyncMock(return_value=conversation)

    with pytest.raises(ConversationStateError, match="active run"):
        await service.retry_failed_run(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
        )

    repository.get_latest_agent_run_for_conversation.assert_not_awaited()
    repository.create_agent_run.assert_not_awaited()
