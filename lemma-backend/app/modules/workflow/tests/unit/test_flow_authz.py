from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.authorization.context import ResourceType
from app.core.authorization.permissions import Permissions
from app.modules.workflow.domain.errors import WorkflowConflictError
from app.modules.workflow.services.workflow_service import WorkflowService
from app.modules.workflow.execution.engine import WorkflowEngine


pytestmark = pytest.mark.asyncio


def _flow_service_with_mocks(authz: AsyncMock) -> WorkflowService:
    service = object.__new__(WorkflowService)
    service.authorization_service = authz
    service.flow_repo = AsyncMock()
    service.uow = SimpleNamespace()
    return service


async def test_flow_service_create_requires_workflow_write():
    ctx = AsyncMock()
    service = _flow_service_with_mocks(AsyncMock())
    pod_id = uuid4()
    requester_user_id = uuid4()
    created = SimpleNamespace(id=uuid4(), pod_id=pod_id)
    service.flow_repo.get_by_name.return_value = None
    service.flow_repo.create.return_value = created

    await service.create_workflow(
        pod_id=pod_id,
        name="flow-authz",
        requester_user_id=requester_user_id,
        ctx=ctx,
    )

    ctx.require.assert_awaited_once()
    call = ctx.require.await_args.args
    assert call[0] == Permissions.WORKFLOW_CREATE
    resource = call[1]
    assert resource.resource_type == ResourceType.POD
    assert resource.pod_id == pod_id
    service.flow_repo.create.assert_awaited_once()


async def test_flow_service_create_persists_inline_graph():
    # A single create call may seed nodes/edges so no separate graph update is
    # required; the graph must land on the persisted entity.
    from app.modules.workflow.domain.graph import WorkflowEdge
    from app.modules.workflow.domain.nodes import (
        EndNode,
        FunctionNode,
        FunctionNodeConfig,
    )

    ctx = AsyncMock()
    service = _flow_service_with_mocks(AsyncMock())
    service.flow_repo.get_by_name.return_value = None
    service.flow_repo.create.side_effect = lambda flow: flow

    nodes = [
        FunctionNode(
            id="save",
            config=FunctionNodeConfig(function_name="save_incoming_email"),
        ),
        EndNode(id="end"),
    ]
    edges = [WorkflowEdge(id="e1", source="save", target="end")]

    await service.create_workflow(
        pod_id=uuid4(),
        name="inline-graph",
        nodes=nodes,
        edges=edges,
        requester_user_id=uuid4(),
        ctx=ctx,
    )

    created = service.flow_repo.create.await_args.args[0]
    assert [n.id for n in created.nodes] == ["save", "end"]
    assert [e.id for e in created.edges] == ["e1"]


async def test_flow_service_create_without_graph_is_shell():
    # Backward compatible: omitting nodes/edges still creates an empty shell.
    ctx = AsyncMock()
    service = _flow_service_with_mocks(AsyncMock())
    service.flow_repo.get_by_name.return_value = None
    service.flow_repo.create.side_effect = lambda flow: flow

    await service.create_workflow(
        pod_id=uuid4(),
        name="shell-only",
        requester_user_id=uuid4(),
        ctx=ctx,
    )

    created = service.flow_repo.create.await_args.args[0]
    assert created.nodes == []
    assert created.edges == []


async def test_flow_service_create_denied_does_not_persist():
    ctx = AsyncMock()
    service = _flow_service_with_mocks(AsyncMock())
    ctx.require.side_effect = PermissionError("denied")

    with pytest.raises(PermissionError):
        await service.create_workflow(
            pod_id=uuid4(),
            name="blocked",
            requester_user_id=uuid4(),
            ctx=ctx,
        )

    service.flow_repo.create.assert_not_called()


async def test_flow_service_create_rejects_duplicate_names():
    service = _flow_service_with_mocks(AsyncMock())
    pod_id = uuid4()
    service.flow_repo.get_by_name.return_value = SimpleNamespace(
        id=uuid4(), pod_id=pod_id
    )

    with pytest.raises(WorkflowConflictError):
        await service.create_workflow(
            pod_id=pod_id,
            name="duplicate-flow",
            requester_user_id=uuid4(),
            ctx=AsyncMock(),
        )

    service.flow_repo.create.assert_not_awaited()


async def test_workflow_engine_read_requires_authz(monkeypatch):
    ctx = AsyncMock()

    class _AuthorizationDataService:
        def __init__(self, session):
            self.session = session

        async def build_user_context(self, *, user_id, pod_id):
            ctx.user_id = user_id
            ctx.pod_id = pod_id
            return ctx

    monkeypatch.setattr(
        "app.modules.workflow.execution.engine.AuthorizationDataService",
        _AuthorizationDataService,
    )
    engine = object.__new__(WorkflowEngine)
    engine.uow = SimpleNamespace(session=object())
    run_id = uuid4()
    pod_id = uuid4()
    flow_id = uuid4()
    engine.run_repo = AsyncMock()
    engine.run_repo.get.return_value = SimpleNamespace(
        id=run_id, pod_id=pod_id, flow_id=flow_id
    )

    run = await engine.get_run(run_id=run_id, requester_user_id=uuid4())

    assert run is not None
    ctx.require.assert_awaited_once()
    assert ctx.require.await_args.args[0] == Permissions.WORKFLOW_READ
