"""A workflow's agent conversation says which workflow it belongs to.

Its title names the agent -- "Workflow run: reviewer" -- so a list of a pod's
runs could tell one workflow's runs from another's only by parsing titles, and
that broke whenever a title changed. The stored metadata now names the workflow
by id and by its stable name, beside the run it was already tied to.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.agent.infrastructure.adapters.workflow_control import (
    AgentControlAdapter,
)
from app.modules.agent.tests.unit.work_recording import RecordingWorkSession
from app.modules.workflow.domain.nodes import AgentNode
from app.modules.workflow.domain.nodes.agent import AgentNodeConfig
from app.modules.workflow.execution.executors.agent import AgentExecutor
from app.modules.workflow.execution.step_context import StepContext


@pytest.mark.anyio
async def test_the_conversation_names_its_workflow_and_keeps_its_origin():
    pod_id = uuid4()
    agent = SimpleNamespace(
        id=uuid4(), pod_id=pod_id, name="reviewer", agent_runtime=None
    )
    adapter = AgentControlAdapter(Mock(session=RecordingWorkSession()))
    adapter.agent_repo = Mock(get_by_pod_and_name=AsyncMock(return_value=agent))
    adapter.conversation_repo = Mock(
        create_conversation=AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        create_agent_run=AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        append_message=AsyncMock(),
        collect_events=Mock(),
    )
    adapter._get_pod_organization_id = AsyncMock(return_value=uuid4())
    adapter._default_agent_runtime_for_pod = AsyncMock(return_value=None)
    step = StepContext(
        run_id=uuid4(),
        flow_id=uuid4(),
        flow_name="pr-review",
        pod_id=pod_id,
        user_id=uuid4(),
        context=SimpleNamespace(resolve_inputs=lambda mapping: {"pr": 7}),
        agent=adapter,
        function=Mock(),
        work=Mock(),
        agent_access=Mock(require_execute=AsyncMock()),
    )
    node = AgentNode(id="review", config=AgentNodeConfig(agent_name="reviewer"))

    await AgentExecutor().execute(node, step)

    conversation = adapter.conversation_repo.create_conversation.await_args.args[0]
    assert conversation.metadata == {
        "source": "WORKFLOW_RUN",
        "workflow_run_id": str(step.run_id),
        "workflow_id": str(step.flow_id),
        "workflow_name": "pr-review",
    }
