from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.infrastructure.harnesses.registry import HarnessRegistry
from app.modules.agent.services.agent_runner_service import (
    AgentRunnerService,
    _finalize_safely,
)


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
async def test_finish_agent_run_swallows_db_errors() -> None:
    """Finalizing a run must never crash the worker, even if the DB is down."""
    service = AgentRunnerService(
        uow_factory=_FailingUowFactory(),
        harness_registry=HarnessRegistry({}),
    )

    # Should not raise.
    await service._finish_agent_run(
        conversation_id=UUID("00000000-0000-0000-0000-000000000001"),
        agent_run_id=UUID("00000000-0000-0000-0000-000000000002"),
        status=AgentRunStatus.FAILED,
        error="Something went wrong",
    )


@pytest.mark.asyncio
async def test_finalize_safely_swallows_exceptions() -> None:
    """_finalize_safely must swallow all errors (DB, cancellation, etc)."""

    async def boom() -> None:
        raise RuntimeError("DB gone away")

    # Should not raise.
    await _finalize_safely(
        boom(), agent_run_id=UUID("00000000-0000-0000-0000-000000000003")
    )


@pytest.mark.asyncio
async def test_finalize_safely_swallows_cancelled_error() -> None:
    """_finalize_safely must swallow asyncio.CancelledError without propagating."""

    async def get_cancelled() -> None:
        raise asyncio.CancelledError()

    # Should not raise — this is the whole point: cancellation during
    # finalization must not crash the worker.
    await _finalize_safely(
        get_cancelled(), agent_run_id=UUID("00000000-0000-0000-0000-000000000004")
    )


@pytest.mark.asyncio
async def test_execute_does_not_re_raise_cancelled_error(monkeypatch) -> None:
    """execute() must swallow CancelledError, not re-raise it.

    Re-raising CancelledError into streaq's `with scope:` block triggers
    "Attempted to exit a cancel scope that isn't the current task's current
    cancel scope" — a RuntimeError that crashes the entire worker. The fix
    is to finalize the run and return normally.
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

    # The critical assertion: execute must NOT re-raise CancelledError.
    # If it does, streaq's scope handling crashes the worker.
    await service.execute(
        agent_run_id=UUID("00000000-0000-0000-0000-000000000020"),
        user_id=UUID("00000000-0000-0000-0000-000000000021"),
        pod_id=UUID("00000000-0000-0000-0000-000000000022"),
        agent_name="test-agent",
    )
