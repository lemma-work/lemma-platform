"""Admission uses the locked workflow limit, not a caller's stale snapshot."""

from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from app.modules.workflow.domain.wait import WaitRequest, WorkflowRunWaitType
from app.modules.workflow.execution.run_admission import queue_wait_if_full


@pytest.mark.asyncio
async def test_a_newly_configured_limit_is_not_bypassed_by_a_stale_unlimited_flow():
    flow_id = uuid4()
    run = SimpleNamespace(id=uuid4())
    stale_flow = SimpleNamespace(id=flow_id, max_concurrent_runs=None)
    locked_flow = SimpleNamespace(id=flow_id, max_concurrent_runs=1)
    engine = SimpleNamespace(
        flow_repo=SimpleNamespace(get_for_update=AsyncMock(return_value=locked_flow)),
        run_repo=SimpleNamespace(count_admitted=AsyncMock(return_value=1)),
    )

    wait = await queue_wait_if_full(engine, stale_flow, run)

    assert isinstance(wait, WaitRequest)
    assert wait.wait_type == WorkflowRunWaitType.QUEUED
    assert wait.external_ref == str(flow_id)
    engine.flow_repo.get_for_update.assert_awaited_once_with(flow_id)
    engine.run_repo.count_admitted.assert_awaited_once_with(
        flow_id, excluding=run.id
    )


@pytest.mark.asyncio
async def test_a_removed_limit_is_respected_even_when_the_caller_has_a_stale_cap():
    flow_id = uuid4()
    run = SimpleNamespace(id=uuid4())
    stale_flow = SimpleNamespace(id=flow_id, max_concurrent_runs=1)
    locked_flow = SimpleNamespace(id=flow_id, max_concurrent_runs=None)
    engine = SimpleNamespace(
        flow_repo=SimpleNamespace(get_for_update=AsyncMock(return_value=locked_flow)),
        run_repo=SimpleNamespace(count_admitted=AsyncMock()),
    )

    wait = await queue_wait_if_full(engine, stale_flow, run)

    assert wait is None
    engine.flow_repo.get_for_update.assert_awaited_once_with(flow_id)
    engine.run_repo.count_admitted.assert_not_awaited()
