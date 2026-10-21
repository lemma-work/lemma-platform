"""What a retry checks before it starts a run: the runtime, usage and access.

Which runs may be retried at all is decided by SQL over the run's messages, so
those cases run against Postgres in `tests/e2e/test_conversation_retry_e2e.py`.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.agent.tests.unit.work_recording import RecordingWorkSession

from app.modules.usage.contracts import UsageLimitExceededError
from app.core.authorization.permissions import Permissions
from app.modules.agent.domain.entities import AgentRun, Conversation, Message
from app.modules.agent.domain.errors import (
    RuntimeUnavailableError,
)
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    AgentRuntimeConfig,
    MessageRole,
)
import app.modules.agent.services.conversation_retry_service as retry_service_module
from app.modules.agent.services.conversation_service import ConversationService


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


class _RuntimeCheck:
    """Records the runtime each retry was checked against, and can refuse it."""

    def __init__(self, refusal: Exception | None = None) -> None:
        self.checked: list[AgentRuntimeConfig] = []
        self._refusal = refusal

    async def __call__(self, _uow, runtime, *, organization_id, user_id) -> None:
        self.checked.append(runtime)
        if self._refusal is not None:
            raise self._refusal


_CURRENT_RUNTIME = AgentRuntimeConfig(profile_id="system:standard", model_name="now")


def _service(
    usage_service: object | None = None,
    runtime_check: _RuntimeCheck | None = None,
):
    repository = SimpleNamespace(
        get_conversation=AsyncMock(),
        lock_conversation=AsyncMock(),
        get_active_agent_run_for_update=AsyncMock(return_value=None),
        get_latest_agent_run_for_conversation=AsyncMock(),
        run_is_replay_safe=AsyncMock(return_value=True),
        create_agent_run=AsyncMock(),
        conversations_with_pending_interaction=AsyncMock(return_value=set()),
    )
    uow = SimpleNamespace(
        collect_events=MagicMock(),
        commit=AsyncMock(),
        # The pod names no default runtime of its own.
        session=RecordingWorkSession(),
    )
    service = ConversationService(
        uow=uow,
        conversation_repository=repository,
        agent_repository=SimpleNamespace(
            get=AsyncMock(
                return_value=SimpleNamespace(name="lem", kind=None, agent_runtime=None)
            )
        ),
        usage_service=usage_service,
        turn_runtime_check=runtime_check or _RuntimeCheck(),
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
async def test_retry_runs_on_the_conversation_s_current_runtime_without_a_new_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check = _RuntimeCheck()
    service, repository, uow = _service(runtime_check=check)
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        agent_runtime=_CURRENT_RUNTIME,
    )
    failed_run = _run(status=AgentRunStatus.FAILED)
    failed_run.conversation_id = conversation.id
    retry_run = _run(status=AgentRunStatus.RUNNING)
    retry_run.conversation_id = conversation.id
    repository.get_latest_agent_run_for_conversation.return_value = failed_run
    repository.run_is_replay_safe.return_value = True
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
        agent_runtime=_CURRENT_RUNTIME,
        metadata={
            "source": "manual_retry",
            "retried_agent_run_id": str(failed_run.id),
        },
    )
    # Re-pointing the teammate at another model is how a retired-model failure
    # is fixed, so the retry must not replay the dead runtime.
    assert check.checked == [_CURRENT_RUNTIME]
    assert uow.session.kinds() == ["agent.turn"]
    uow.commit.assert_awaited_once()
    # Asked about this one run, not handed every run of the conversation.
    repository.run_is_replay_safe.assert_awaited_once_with(failed_run.id)
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


def _metered_retry_setup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    allowed: bool,
) -> tuple[ConversationService, SimpleNamespace, AgentRun, Conversation]:
    usage_service = _usage_service(allowed=allowed)
    service, repository, _ = _service(usage_service)
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        agent_runtime=_CURRENT_RUNTIME,
    )
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
async def test_retry_failed_run_asks_usage_about_the_runtime_it_will_run_on(
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
        == _CURRENT_RUNTIME
    )


@pytest.mark.asyncio
async def test_a_retry_on_a_model_that_is_gone_is_refused_before_a_run_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refusal = RuntimeUnavailableError("gone")
    service, repository, uow = _service(runtime_check=_RuntimeCheck(refusal))
    conversation = Conversation(
        pod_id=uuid4(), user_id=uuid4(), agent_runtime=_CURRENT_RUNTIME
    )
    failed_run = _run(status=AgentRunStatus.FAILED)
    repository.get_latest_agent_run_for_conversation.return_value = failed_run
    _authorize(monkeypatch, conversation)

    with pytest.raises(RuntimeUnavailableError):
        await service.retry_failed_run(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
        )

    repository.create_agent_run.assert_not_awaited()
    uow.commit.assert_not_awaited()


def _bare_failed_run(metadata: dict[str, object] | None) -> AgentRun:
    return AgentRun(
        conversation_id=uuid4(),
        status=AgentRunStatus.FAILED,
        agent_runtime=_CURRENT_RUNTIME,
        started_at=datetime.now(timezone.utc),
        metadata=metadata,
    )


def test_a_failed_retry_owning_no_messages_is_safely_retryable() -> None:
    """The turn a retry replays belongs to the run it retried."""
    assert _bare_failed_run({"source": "manual_retry"}).is_safely_retryable


def test_a_failed_run_with_no_turn_and_no_retry_behind_it_is_not() -> None:
    assert not _bare_failed_run({"source": "user_message"}).is_safely_retryable
    assert not _bare_failed_run(None).is_safely_retryable


def test_a_failed_retry_that_replied_is_not_safely_retryable() -> None:
    retry = _bare_failed_run({"source": "manual_retry"})
    retry.messages = [
        Message.create(
            conversation_id=retry.conversation_id,
            sequence=1,
            agent_run_id=retry.id,
            role=MessageRole.ASSISTANT,
            text="Here is half",
        )
    ]
    assert not retry.is_safely_retryable
