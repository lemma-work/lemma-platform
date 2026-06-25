from __future__ import annotations

from uuid import UUID

import pytest

from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.infrastructure.harnesses.registry import HarnessRegistry
from app.modules.agent.services.agent_runner_service import AgentRunnerService


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
