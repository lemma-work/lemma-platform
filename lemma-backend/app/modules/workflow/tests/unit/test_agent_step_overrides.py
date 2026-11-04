"""An agent step's own instructions, model, generation settings and timeout.

Before, an agent node carried only the agent's name and its input: a workflow
could not tell the agent what this step was for, run it on a smaller model, or
turn its reasoning down, and every step shared the deployment's wait ceiling.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.core.domain.generation import GenerationSettings, ReasoningEffort
from app.modules.workflow.domain.nodes import AgentNode
from app.modules.workflow.domain.nodes.agent import AgentNodeConfig
from app.modules.workflow.execution.executors.agent import AgentExecutor


class _RecordingAgentPort:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.conversation_id = uuid4()

    async def run_agent(self, **kwargs: object) -> UUID:
        self.calls.append(kwargs)
        return self.conversation_id


class _AllowsEveryAgent:
    async def require_execute(self, **_: object) -> None:
        return None


def _step(agent: _RecordingAgentPort) -> SimpleNamespace:
    return SimpleNamespace(
        agent_access=_AllowsEveryAgent(),
        context=SimpleNamespace(resolve_inputs=lambda mapping: {"ticket": 7}),
        agent=agent,
        pod_id=uuid4(),
        user_id=uuid4(),
        run_id=uuid4(),
        flow_id=uuid4(),
        flow_name="triage-flow",
    )


@pytest.mark.asyncio
async def test_the_step_hands_its_overrides_to_the_agent_and_its_timeout_to_the_wait():
    node = AgentNode(
        id="triage",
        config=AgentNodeConfig(
            agent_name="triage",
            instructions="Only classify; do not reply.",
            model_name="small",
            generation=GenerationSettings(reasoning_effort=ReasoningEffort.LOW),
            timeout_seconds=900,
        ),
    )
    agent = _RecordingAgentPort()

    outcome = await AgentExecutor().execute(node, _step(agent))

    (call,) = agent.calls
    assert call["instructions"] == "Only classify; do not reply."
    assert call["model_name"] == "small"
    assert call["generation"] == GenerationSettings(
        reasoning_effort=ReasoningEffort.LOW
    )
    assert outcome.wait.payload == {"agent_name": "triage", "timeout_seconds": 900}


@pytest.mark.asyncio
async def test_a_plain_step_changes_nothing():
    node = AgentNode(id="triage", config=AgentNodeConfig(agent_name="triage"))
    agent = _RecordingAgentPort()

    outcome = await AgentExecutor().execute(node, _step(agent))

    (call,) = agent.calls
    assert (call["instructions"], call["model_name"], call["generation"]) == (
        None,
        None,
        None,
    )
    assert outcome.wait.payload == {"agent_name": "triage"}


def test_a_timeout_shorter_than_the_sweep_can_honour_is_refused():
    with pytest.raises(ValidationError):
        AgentNodeConfig(agent_name="triage", timeout_seconds=60)


def test_unknown_generation_fields_are_refused_when_the_workflow_is_saved():
    with pytest.raises(ValidationError):
        AgentNodeConfig.model_validate(
            {"agent_name": "triage", "generation": {"effort": "low"}}
        )
