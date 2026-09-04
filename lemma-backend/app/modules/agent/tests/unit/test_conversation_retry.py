from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.usage.contracts import UsageLimitExceededError
from app.core.authorization.permissions import Permissions
from app.modules.agent.domain.entities import AgentRun, Conversation, Message
from app.modules.agent.domain.errors import ConversationStateError
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    AgentRuntimeConfig,
    MessageRole,
)
import app.modules.agent.services.conversation_queries as queries
import app.modules.agent.services.conversation_retry_service as retry_service_module
from app.modules.agent.services.conversation_retry_service import (
    ConversationRetryService,
)


def _run(
    *,
    status: AgentRunStatus,
    metadata: dict[str, object] | None = None,
    profile_id: str = "user:harness",
) -> AgentRun:
    run = AgentRun(
        conversation_id=uuid4(),
        status=status,
        agent_runtime=AgentRuntimeConfig(
            profile_id=profile_id,
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


def _service(usage_service: object | None = None):
    repository = SimpleNamespace(
        get_conversation=AsyncMock(),
        lock_conversation=AsyncMock(),
        get_active_agent_run_for_update=AsyncMock(return_value=None),
        get_latest_agent_run_for_conversation=AsyncMock(),
        run_has_only_user_messages=AsyncMock(return_value=True),
        create_agent_run=AsyncMock(),
    )
    uow = SimpleNamespace(collect_events=MagicMock(), commit=AsyncMock())
    service = ConversationRetryService(
        uow=uow,
        conversation_repository=repository,
        agent_repository=SimpleNamespace(),
        authorization_service=SimpleNamespace(),
        usage_service=usage_service,
    )
    return service, repository, uow


def _authorize(
    monkeypatch: pytest.MonkeyPatch, conversation: Conversation
) -> AsyncMock:
    """Stand in for the access check at the seam the service actually imports.

    `monkeypatch.setattr` on the module, deliberately, rather than an attribute
    assigned onto the service: setattr refuses to patch a name that is not
    there, so moving `authorized_conversation` out from under this call site
    fails the tests instead of passing them. Hand-assigning it onto the instance
    is what let the real call site go missing for a release -- the stub invented
    the very attribute production was short of.
    """
    patched = AsyncMock(return_value=conversation)
    monkeypatch.setattr(retry_service_module, "authorized_conversation", patched)
    return patched


def _usage_service(*, allowed: bool) -> SimpleNamespace:
    return SimpleNamespace(
        get_usage_limits=AsyncMock(
            return_value={
                "allowed": allowed,
                "org_monthly": {"allowed": allowed},
                "user_weekly": {"allowed": True},
                "user_monthly": {"allowed": True},
            }
        )
    )


@pytest.mark.asyncio
async def test_retry_failed_run_reuses_runtime_without_appending_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    repository.run_has_only_user_messages.return_value = True
    repository.create_agent_run.return_value = retry_run
    authorize = _authorize(monkeypatch, conversation)

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
    # Asked about this one run, not handed every run of the conversation.
    repository.run_has_only_user_messages.assert_awaited_once_with(failed_run.id)
    # Retrying runs the agent again, so it demands execute rather than read.
    authorize.assert_awaited_once_with(
        repository,
        service.agent_repository,
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
        agent_name=None,
        action=Permissions.AGENT_EXECUTE,
    )


@pytest.mark.asyncio
async def test_retry_failed_run_rejects_non_failed_latest_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, _ = _service()
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())
    repository.get_latest_agent_run_for_conversation.return_value = _run(
        status=AgentRunStatus.COMPLETED
    )
    _authorize(monkeypatch, conversation)

    with pytest.raises(ConversationStateError, match="did not fail"):
        await service.retry_failed_run(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
        )

    repository.create_agent_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_failed_run_rejects_an_active_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, _ = _service()
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())
    repository.get_active_agent_run_for_update.return_value = _run(
        status=AgentRunStatus.RUNNING
    )
    _authorize(monkeypatch, conversation)

    with pytest.raises(ConversationStateError, match="active run"):
        await service.retry_failed_run(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
        )

    repository.get_latest_agent_run_for_conversation.assert_not_awaited()
    repository.create_agent_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_failed_run_returns_active_manual_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, uow = _service()
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())
    active_retry = _run(
        status=AgentRunStatus.RUNNING,
        metadata={"source": "manual_retry"},
    )
    active_retry.conversation_id = conversation.id
    repository.get_active_agent_run_for_update.return_value = active_retry
    _authorize(monkeypatch, conversation)

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
async def test_retry_failed_run_rejects_failed_run_with_non_user_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    # The run said something, so the database reports it is not replay-safe.
    repository.run_has_only_user_messages.return_value = False
    _authorize(monkeypatch, conversation)

    with pytest.raises(ConversationStateError, match="retried safely"):
        await service.retry_failed_run(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
        )

    repository.create_agent_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_failed_run_requires_a_persisted_user_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, _ = _service()
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())
    failed_run = _run(status=AgentRunStatus.FAILED)
    failed_run.conversation_id = conversation.id
    failed_run.messages = []
    repository.get_latest_agent_run_for_conversation.return_value = failed_run
    # No messages at all: there is no user turn to replay.
    repository.run_has_only_user_messages.return_value = False
    _authorize(monkeypatch, conversation)

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
    monkeypatch: pytest.MonkeyPatch,
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
    repository.run_has_only_user_messages.return_value = not has_non_user_activity
    monkeypatch.setattr(
        queries, "resolve_expected_agent_id", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(queries, "require_agent_action", AsyncMock())

    result = await service.queries.get_conversation(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
    )

    assert result.last_run_retryable is expected_retryable


def _metered_retry_setup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    allowed: bool,
) -> tuple[ConversationRetryService, SimpleNamespace, AgentRun, Conversation]:
    usage_service = _usage_service(allowed=allowed)
    service, repository, _ = _service(usage_service)
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4(), agent_id=uuid4())
    # A "system:" profile is the metered one -- a user's own key is not counted,
    # so only this profile makes the preflight consult the usage service at all.
    failed_run = _run(status=AgentRunStatus.FAILED, profile_id="system:standard")
    failed_run.conversation_id = conversation.id
    repository.get_latest_agent_run_for_conversation.return_value = failed_run
    repository.create_agent_run.return_value = _run(status=AgentRunStatus.RUNNING)
    _authorize(monkeypatch, conversation)
    return service, repository, failed_run, conversation


@pytest.mark.asyncio
async def test_retry_failed_run_refuses_when_the_usage_limit_is_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry is a fresh billable run, so it answers to the same ceiling.

    The guard for a retry path that reached for the preflight on `self` when it
    lives on the turn coordinator: the call raised AttributeError before usage
    was ever consulted, which a stub on the instance hid rather than caught.
    """
    service, repository, _, _ = _metered_retry_setup(monkeypatch, allowed=False)

    with pytest.raises(UsageLimitExceededError, match="organization monthly"):
        await service.retry_failed_run(
            conversation_id=uuid4(),
            user_id=uuid4(),
            pod_id=uuid4(),
        )

    repository.create_agent_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_failed_run_asks_about_the_failed_run_s_own_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, failed_run, conversation = _metered_retry_setup(
        monkeypatch, allowed=True
    )
    user_id = conversation.user_id

    await service.retry_failed_run(
        conversation_id=conversation.id,
        user_id=user_id,
        pod_id=conversation.pod_id,
    )

    assert service.usage_service is not None
    service.usage_service.get_usage_limits.assert_awaited_once_with(
        organization_id=conversation.organization_id,
        user_id=user_id,
    )
    repository.create_agent_run.assert_awaited_once()
    assert (
        repository.create_agent_run.await_args.kwargs["agent_runtime"]
        is failed_run.agent_runtime
    )
