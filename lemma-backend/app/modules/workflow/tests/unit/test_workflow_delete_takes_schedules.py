"""Deleting a workflow must take the schedules pointing at it.

The agent half of this lives in
``app/modules/agent/tests/unit/test_agent_delete_revocation.py``; a schedule
fires at one of the two, and either left behind is the same dangling target.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.workflow.services.workflow_service import WorkflowService


def _service(monkeypatch, *, flow, teardown) -> WorkflowService:
    service = WorkflowService(AsyncMock(), schedule_teardown=teardown)
    # Built in `__init__` from the unit of work, so it is replaced rather than
    # injected. The repository itself is exercised against a real database
    # elsewhere.
    service.flow_repo = AsyncMock()
    service.flow_repo.get = AsyncMock(return_value=flow)
    return service


@pytest.mark.asyncio
async def test_deleting_a_workflow_takes_its_schedules(monkeypatch):
    """`PS-SCHED-030` asks for the dangling target to be prevented.

    A schedule left pointing at a deleted workflow fires on a timer forever,
    and nothing downstream can explain why it did nothing.
    """
    flow = SimpleNamespace(id=uuid4(), user_id=uuid4(), pod_id=uuid4(), icon_url=None)
    teardown = SimpleNamespace(remove_all_for_workflow=AsyncMock(return_value=1))
    service = _service(monkeypatch, flow=flow, teardown=teardown)

    await service.delete_workflow(flow.id)

    teardown.remove_all_for_workflow.assert_awaited_once_with(flow.id)


@pytest.mark.asyncio
async def test_the_schedules_go_before_the_workflow_row_does(monkeypatch):
    """Order is the trick, as it is for surfaces on the agent side.

    The schedules are found *by* ``workflow_id``. Deleting the workflow first
    leaves a window in which a request racing this one can point a new schedule
    at a workflow that is already gone.
    """
    flow = SimpleNamespace(id=uuid4(), user_id=uuid4(), pod_id=uuid4(), icon_url=None)
    order: list[str] = []
    teardown = SimpleNamespace(
        remove_all_for_workflow=AsyncMock(
            side_effect=lambda *a, **k: order.append("schedules") or 1
        )
    )
    service = _service(monkeypatch, flow=flow, teardown=teardown)
    service.flow_repo.delete = AsyncMock(
        side_effect=lambda _id: order.append("workflow")
    )

    await service.delete_workflow(flow.id)

    assert order == ["schedules", "workflow"], (
        f"the workflow row must go last, or its schedules are unfindable: {order}"
    )


@pytest.mark.asyncio
async def test_a_missing_workflow_asks_the_schedule_module_for_nothing(monkeypatch):
    """Deleting what is not there stays a no-op rather than a wide delete.

    ``remove_all_for_workflow(None)`` would match every schedule with no
    workflow target — which is every agent schedule in the deployment.
    """
    teardown = SimpleNamespace(remove_all_for_workflow=AsyncMock())
    service = _service(monkeypatch, flow=None, teardown=teardown)

    await service.delete_workflow(uuid4())

    teardown.remove_all_for_workflow.assert_not_awaited()
