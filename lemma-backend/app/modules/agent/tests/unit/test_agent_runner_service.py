from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.harnesses.registry import HarnessRegistry
from app.modules.agent.services import run_finalizer as finalizer_module
from app.modules.agent.services.run_finalizer import (
    finalize_safely,
    rejected_run_error_message,
)
from app.modules.agent.services.run_identity import RunIdentity
from app.modules.agent.services.agent_runner_service import (
    AgentRunnerService,
    _run_input_text,
)
from app.modules.test_support.fakes import FakeUnitOfWork


_GENERIC_REJECTION = "The Agent Host rejected this run before dispatch. Try again."


def test_rejected_run_error_message_uses_the_harness_supplied_detail():
    assert (
        rejected_run_error_message({"detail": "Harness snapshot is stale; refresh it"})
        == "Harness snapshot is stale; refresh it"
    )


def test_rejected_run_error_message_falls_back_for_malformed_data():
    assert rejected_run_error_message("not-a-dict") == _GENERIC_REJECTION
    assert (
        rejected_run_error_message({"reason": "something_else"}) == _GENERIC_REJECTION
    )
    assert rejected_run_error_message({"detail": "   "}) == _GENERIC_REJECTION


class _FailingContextManager:
    """Async context manager that raises on enter, simulating a dead DB session."""

    async def __aenter__(self) -> None:
        raise RuntimeError("db connection lost during shutdown")

    async def __aexit__(self, *args: object) -> None:
        pass


class _FailingUowFactory:
    """Simulates a DB connection that is already closing during worker shutdown."""

    def __call__(self) -> _FailingContextManager:
        return _FailingContextManager()


@pytest.mark.asyncio
async def test_finish_agent_run_rethrows_db_errors_for_boundary_retry() -> None:
    """Infrastructure failure must reach the finalization process boundary."""
    service = AgentRunnerService(
        uow_factory=_FailingUowFactory(),
        harness_registry=HarnessRegistry({}),
    )

    with pytest.raises(RuntimeError, match="db connection lost"):
        await service.finalizer.finish(
            run=RunIdentity(
                conversation_id=UUID("00000000-0000-0000-0000-000000000001"),
                agent_run_id=UUID("00000000-0000-0000-0000-000000000002"),
            ),
            status=AgentRunStatus.FAILED,
            error="Something went wrong",
        )


@pytest.mark.asyncio
async def test_finish_agent_run_uses_committed_terminal_state_and_collects_event(
    monkeypatch,
) -> None:
    """Completion publication must reflect the state won by the DB transition."""
    uow = FakeUnitOfWork()

    class _Factory:
        def __call__(self):
            return uow

    finish_result = SimpleNamespace(
        updated=True,
        status=AgentRunStatus.STOPPED,
        conversation_status=ConversationStatus.STOPPED,
    )
    repository = SimpleNamespace(finish_agent_run=AsyncMock(return_value=finish_result))
    monkeypatch.setattr(
        finalizer_module, "ConversationRepository", lambda _uow: repository
    )
    publish = AsyncMock()
    monkeypatch.setattr(finalizer_module, "publish_conversation_event", publish)

    service = AgentRunnerService(
        uow_factory=_Factory(),
        harness_registry=HarnessRegistry({}),
    )
    publish_usage = AsyncMock()
    monkeypatch.setattr(service.finalizer, "publish_usage", publish_usage)
    conversation_id = UUID("00000000-0000-0000-0000-000000000101")
    run_id = UUID("00000000-0000-0000-0000-000000000102")

    await service.finalizer.finish(
        run=RunIdentity(conversation_id=conversation_id, agent_run_id=run_id),
        status=AgentRunStatus.FAILED,
        conversation_status=ConversationStatus.FAILED,
        error="stale pre-transition error",
        output_data={"partial": True},
    )

    assert uow.committed is True
    assert len(uow.collected_events) == 1
    event = uow.collected_events[0]
    assert event.status is AgentRunStatus.STOPPED
    assert event.data == {
        "output_data": {"partial": True},
        "conversation_status": "STOPPED",
    }
    assert publish.await_count == 1
    assert publish.await_args.args[1]["type"] == "completed"
    publish_usage.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_safely_swallows_exceptions() -> None:
    """finalize_safely must swallow all errors (DB, cancellation, etc)."""

    async def boom() -> None:
        raise RuntimeError("DB gone away")

    # Should not raise.
    await finalize_safely(
        boom(), agent_run_id=UUID("00000000-0000-0000-0000-000000000003")
    )


@pytest.mark.asyncio
async def test_finalize_safely_swallows_cancelled_error() -> None:
    """finalize_safely must swallow asyncio.CancelledError without propagating."""

    async def get_cancelled() -> None:
        raise asyncio.CancelledError()

    # Should not raise — this is the whole point: cancellation during
    # finalization must not crash the worker.
    await finalize_safely(
        get_cancelled(), agent_run_id=UUID("00000000-0000-0000-0000-000000000004")
    )


@pytest.mark.asyncio
async def test_execute_re_raises_cancelled_error(monkeypatch) -> None:
    """execute() must let CancelledError out, not swallow it.

    This is the whole resume mechanism. streaq XACKs a task that returned and
    "relinquishes" a cancelled one -- leaving it in the pending list for the
    next worker to reclaim. Swallowing it made every run interrupted by a
    deploy look like a success, so nothing redelivered it and each person had
    to ask again.

    This test previously asserted the opposite, on the grounds that re-raising
    crashes the worker with "Attempted to exit a cancel scope that isn't the
    current task's current cancel scope". Reproduced against a real worker
    under SIGTERM, with and without a shielded cleanup, that did not happen:
    the task was relinquished and a fresh worker reclaimed it.
    """
    service = AgentRunnerService(
        uow_factory=_FailingUowFactory(),
        harness_registry=HarnessRegistry({}),
    )

    # Build minimal domain objects so _load_run_context returns valid values.
    conversation = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000010"),
        organization_id=UUID("00000000-0000-0000-0000-000000000011"),
        pod_id=UUID("00000000-0000-0000-0000-000000000012"),
        agent_id=UUID("00000000-0000-0000-0000-000000000013"),
    )
    agent = SimpleNamespace(name="test-agent")
    agent_run = SimpleNamespace(
        started_at=None,
        status=AgentRunStatus.RUNNING,
        agent_runtime=None,
        error=None,
    )

    async def fake_load(*args, **kwargs):
        return conversation, agent, agent_run, []

    monkeypatch.setattr(service, "_load_run_context", fake_load)

    # Make the harness.run / inner try block raise CancelledError by patching
    # _resolve_agent_runtime to cancel the current task mid-flight.
    async def fake_resolve(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(service, "_resolve_agent_runtime", fake_resolve)

    # The critical assertion: it must reach streaq, or the job is acked and
    # the run is lost.
    with pytest.raises(asyncio.CancelledError):
        await service.execute(
            agent_run_id=UUID("00000000-0000-0000-0000-000000000020"),
            user_id=UUID("00000000-0000-0000-0000-000000000021"),
            pod_id=UUID("00000000-0000-0000-0000-000000000022"),
            agent_name="test-agent",
        )


def _message(role: str, kind: MessageKind, text: str | None) -> Message:
    return Message(
        id=uuid4(),
        created_at=datetime.now(UTC),
        conversation_id=uuid4(),
        sequence=0,
        agent_run_id=None,
        role=role,
        kind=kind,
        text=text,
    )


def test_run_input_text_is_the_turn_that_started_the_run():
    """The span's input is this turn, not the transcript it was handed."""
    messages = [
        _message(MessageRole.USER.value, MessageKind.TEXT, "the previous turn"),
        _message(MessageRole.ASSISTANT.value, MessageKind.TEXT, "an earlier answer"),
        _message(MessageRole.USER.value, MessageKind.TEXT, "what changed?"),
        _message(MessageRole.TOOL.value, MessageKind.TOOL_RETURN, "tool output"),
    ]
    assert _run_input_text(messages) == "what changed?"


def test_run_input_text_skips_non_textual_and_blank_user_messages():
    assert _run_input_text([]) is None
    assert (
        _run_input_text(
            [_message(MessageRole.ASSISTANT.value, MessageKind.TEXT, "only the agent")]
        )
        is None
    )
    assert (
        _run_input_text(
            [
                _message(MessageRole.USER.value, MessageKind.TEXT, "the real prompt"),
                _message(MessageRole.USER.value, MessageKind.TEXT, "   "),
                _message(MessageRole.USER.value, MessageKind.THINKING, "not a prompt"),
            ]
        )
        == "the real prompt"
    )
