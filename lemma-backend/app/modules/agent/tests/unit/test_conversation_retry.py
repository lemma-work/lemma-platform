from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.agent.domain.entities import AgentRun, Conversation, Message
from app.modules.agent.domain.errors import ConversationStateError
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    AgentRuntimeConfig,
    MessageRole,
)
from app.modules.agent.services.conversation_retry_service import (
    ConversationRetryService,
)


def _run(
    *,
    status: AgentRunStatus,
    metadata: dict[str, object] | None = None,
) -> AgentRun:
    run = AgentRun(
        conversation_id=uuid4(),
        status=status,
        agent_runtime=AgentRuntimeConfig(
            profile_id="user:daemon",
            model_name="claude-sonnet-4-5",
        ),
        started_at=datetime.now(timezone.utc),
        metadata=metadata,
    )
    if status == AgentRunStatus.FAILED:
        run.messages = [
            Message.create(
                conversation_id=run.conversation_id,
                sequence=0,
                agent_run_id=run.id,
                role=MessageRole.USER,
                text="finish the report",
            )
        ]
    return run


def _service():
    repository = SimpleNamespace(
        get_conversation=AsyncMock(),
        lock_conversation=AsyncMock(),
        get_active_agent_run_for_update=AsyncMock(return_value=None),
        get_latest_agent_run_for_conversation=AsyncMock(),
        list_agent_runs_with_messages_by_run_id=AsyncMock(),
        create_agent_run=AsyncMock(),
    )
    uow = SimpleNamespace(collect_events=MagicMock(), commit=AsyncMock())
    service = ConversationRetryService(
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
    repository.list_agent_runs_with_messages_by_run_id.return_value = [failed_run]
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
    repository.list_agent_runs_with_messages_by_run_id.assert_awaited_once_with(
        failed_run.id
    )


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


@pytest.mark.asyncio
async def test_retry_failed_run_returns_active_manual_retry() -> None:
    service, repository, uow = _service()
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())
    active_retry = _run(
        status=AgentRunStatus.RUNNING,
        metadata={"source": "manual_retry"},
    )
    active_retry.conversation_id = conversation.id
    repository.get_active_agent_run_for_update.return_value = active_retry
    service._authorized_conversation = AsyncMock(return_value=conversation)

    result = await service.retry_failed_run(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
    )

    assert result.agent_run_id == active_retry.id
    assert result.started_new_run is False
    repository.get_latest_agent_run_for_conversation.assert_not_awaited()
    repository.create_agent_run.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_failed_run_rejects_failed_run_with_non_user_activity() -> None:
    service, repository, _ = _service()
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())
    failed_run = _run(status=AgentRunStatus.FAILED)
    failed_run.conversation_id = conversation.id
    repository.get_latest_agent_run_for_conversation.return_value = failed_run
    failed_run.messages.append(
        Message.create(
            conversation_id=conversation.id,
            sequence=1,
            agent_run_id=failed_run.id,
            role=MessageRole.ASSISTANT,
            text="partial output",
        )
    )
    repository.list_agent_runs_with_messages_by_run_id.return_value = [failed_run]
    service._authorized_conversation = AsyncMock(return_value=conversation)

    with pytest.raises(ConversationStateError, match="retried safely"):
        await service.retry_failed_run(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
        )

    repository.create_agent_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_failed_run_requires_a_persisted_user_turn() -> None:
    service, repository, _ = _service()
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())
    failed_run = _run(status=AgentRunStatus.FAILED)
    failed_run.conversation_id = conversation.id
    failed_run.messages = []
    repository.get_latest_agent_run_for_conversation.return_value = failed_run
    repository.list_agent_runs_with_messages_by_run_id.return_value = [failed_run]
    service._authorized_conversation = AsyncMock(return_value=conversation)

    with pytest.raises(ConversationStateError, match="retried safely"):
        await service.retry_failed_run(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
        )

    repository.create_agent_run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("has_non_user_activity", "expected_retryable"),
    [(False, True), (True, False)],
)
async def test_conversation_detail_reports_persisted_retryability(
    has_non_user_activity: bool,
    expected_retryable: bool,
) -> None:
    service, repository, _ = _service()
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())
    failed_run = _run(status=AgentRunStatus.FAILED)
    failed_run.conversation_id = conversation.id
    if has_non_user_activity:
        failed_run.messages.append(
            Message.create(
                conversation_id=conversation.id,
                sequence=1,
                agent_run_id=failed_run.id,
                role=MessageRole.TOOL,
                text="tool activity",
            )
        )
    conversation.agent_runs = [failed_run]
    conversation.last_run_status = AgentRunStatus.FAILED
    repository.get_conversation.return_value = conversation
    service._expected_agent_id = AsyncMock(return_value=None)
    service._require_agent_action = AsyncMock()

    result = await service.get_conversation(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
    )

    assert result.last_run_retryable is expected_retryable
