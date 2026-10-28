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

from app.modules.agent.application.automation.start import StartAutomatedRun
from app.modules.agent.contracts.automation import _AgentDriver
from app.modules.agent.domain.value_objects import ConversationOrigin
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
    starts = StartAutomatedRun(
        Mock(session=RecordingWorkSession()),
        default_runtime=AsyncMock(return_value=None),
    )
    starts.conversations = Mock(
        create_conversation=AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    )
    starts.runs = Mock(
        create_agent_run=AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    )
    starts.messages = Mock(append_message=AsyncMock())
    starts.organization_of = AsyncMock(return_value=uuid4())  # type: ignore[method-assign]
    adapter = object.__new__(_AgentDriver)
    adapter.agents = Mock(get_by_pod_and_name=AsyncMock(return_value=agent))
    adapter.starts = starts
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

    conversation = starts.conversations.create_conversation.await_args.args[0]
    # Where it came from is the conversation's origin and its workflow run;
    # which workflow, the tags beside them.
    assert conversation.origin is ConversationOrigin.WORKFLOW_STEP
    assert conversation.workflow_run_id == step.run_id
    assert conversation.metadata == {
        "workflow_id": str(step.flow_id),
        "workflow_name": "pr-review",
    }
