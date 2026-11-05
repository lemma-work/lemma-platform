"""An agent step asks ``agent.execute`` of the run's person before it starts.

A function step re-checked ``function.execute`` at dispatch; an agent step did
not, so a workflow anyone could start ran its agent node's agent for them even
when they could not run that agent themselves (M18).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.domain.errors import DomainError
from app.modules.workflow.domain.graph import WorkflowEdge
from app.modules.workflow.domain.nodes import AgentNode, AgentNodeConfig, EndNode
from app.modules.workflow.domain.run import WorkflowRunEntity, WorkflowRunStatus
from app.modules.workflow.domain.workflow import WorkflowEntity
from app.modules.workflow.execution.stepper import RunStepper

pytestmark = pytest.mark.asyncio


class _RecordingAgentPort:
    def __init__(self) -> None:
        self.started: list[str] = []

    async def run_agent(self, agent_name: str, *_: object, **__: object) -> UUID:
        self.started.append(agent_name)
        return uuid4()


class _RefusesAgent:
    """Answers the way ``ctx.require`` does for a person without the grant."""

    def __init__(self, refused: str) -> None:
        self.refused = refused
        self.asked: list[tuple[str, UUID]] = []

    async def require_execute(
        self, *, agent_name: str, pod_id: UUID, user_id: UUID
    ) -> None:
        self.asked.append((agent_name, user_id))
        if agent_name == self.refused:
            raise DomainError(
                "Permission denied: agent.execute",
                code="PERMISSION_DENIED",
                status_code=403,
            )


def _flow_with_agent_step(agent_name: str) -> WorkflowEntity:
    flow = WorkflowEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="agent-step-flow",
        nodes=[
            AgentNode(id="ask", config=AgentNodeConfig(agent_name=agent_name)),
            EndNode(id="end"),
        ],
        edges=[WorkflowEdge(id="e1", source="ask", target="end")],
    )
    flow.validate_graph()
    return flow


def _run(flow: WorkflowEntity) -> WorkflowRunEntity:
    return WorkflowRunEntity.create(
        flow_id=flow.id,
        pod_id=flow.pod_id,
        user_id=uuid4(),
        entry_node_id=flow.entry_node_id,
    )


async def test_an_agent_the_runs_person_may_not_execute_fails_the_step_unstarted():
    flow = _flow_with_agent_step("restricted_agent")
    run = _run(flow)
    agent = _RecordingAgentPort()
    access = _RefusesAgent("restricted_agent")

    await RunStepper(
        agent=agent,  # type: ignore[arg-type]
        function=None,  # type: ignore[arg-type]
        work=None,  # type: ignore[arg-type]
        agent_access=access,
    ).advance(run, flow)

    assert run.status == WorkflowRunStatus.FAILED
    assert run.failed_node_id == "ask"
    assert "agent.execute" in (run.error or "")
    assert agent.started == []
    # Asked as the run's person, not as whoever happened to drive the advance.
    assert access.asked == [("restricted_agent", run.user_id)]


async def test_an_agent_the_runs_person_may_execute_is_started():
    flow = _flow_with_agent_step("open_agent")
    run = _run(flow)
    agent = _RecordingAgentPort()

    await RunStepper(
        agent=agent,  # type: ignore[arg-type]
        function=None,  # type: ignore[arg-type]
        work=None,  # type: ignore[arg-type]
        agent_access=_RefusesAgent("someone_else"),
    ).advance(run, flow)

    assert run.status == WorkflowRunStatus.RUNNING
    assert agent.started == ["open_agent"]


async def test_a_stepper_with_no_access_check_refuses_agent_steps_rather_than_skip_it():
    flow = _flow_with_agent_step("any_agent")
    run = _run(flow)
    agent = _RecordingAgentPort()

    await RunStepper(
        agent=agent,  # type: ignore[arg-type]
        function=None,  # type: ignore[arg-type]
        work=None,  # type: ignore[arg-type]
    ).advance(run, flow)

    assert run.status == WorkflowRunStatus.FAILED
    assert agent.started == []
