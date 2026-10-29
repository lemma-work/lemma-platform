"""How many statements a run issues before its model is first called.

Everything a run reads to start -- its conversation, agent and history, its
runtime, its grants, the context its tools see and the toolsets it is offered
-- is loaded in one place, once. Counted in statements rather than time: an
extra read per start looks like nothing at the call site and like everything
under load, and it does not depend on the machine CI happens to run on.

The number is pinned, not bounded: a change that saves a read should lower it
here on purpose, and a change that adds one should have to say so.
"""

from __future__ import annotations

from functools import partial
from uuid import UUID

import pytest

from app.modules.agent.services.agent_runner_service import AgentRunnerService
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.infrastructure.harness.registry import build_harness_registry
from app.modules.agent.services.run_session import RunSession
from app.modules.agent.services.tool_catalog import ToolSurface, specs_for
from app.modules.agent.tests.e2e.turn_work_support import TurnPod, scalar, turn_pod
from app.modules.agent.services.run_log_loader import load_run_log
from app.modules.test_support.query_counting import counted_queries, format_statements

pytestmark = [pytest.mark.e2e]

#: Statements one run start issues on a fresh pod with the POD toolset. It was
#: 16 when four places assembled a run's context; the session reads the agent's
#: grants once, for its toolsets and its brief, where they were read twice; and
#: one more reads where agent_surfaces heard the conversation, which was keys of
#: the conversation's row before it was agent_surfaces' own table.
#: Two more read the run log the model's history is replayed from (18). Then
#: the history window became one statement where it was four -- the run's
#: conversation, its newest runs, their total and each run's message count --
#: the log's checkpoint is found in the statement that reads the steps after
#: it, and the brief's schedules come with their count: 13. (A third log read,
#: the last logged run's status, only once there are steps.)
RUN_START_STATEMENTS = 13


@pytest.fixture
async def pod(authenticated_client, fixed_test_org) -> TurnPod:
    return await turn_pod(authenticated_client, fixed_test_org["id"], toolsets=["POD"])


async def _start_run(run_id: UUID, *, user_id: UUID, pod_id: UUID, agent_name: str):
    """What ``AgentRunnerService.execute`` does before the harness is called."""
    uow_factory = SessionUnitOfWorkFactory(async_session_maker)
    runner = AgentRunnerService(
        uow_factory=uow_factory, harness_registry=build_harness_registry()
    )
    session = await RunSession.load(
        uow_factory,
        agent_run_id=run_id,
        user_id=user_id,
        pod_id=pod_id,
        agent_name=agent_name,
    )
    resolved = await session.resolve_turn_runtime(
        user_id=user_id,
        uow_factory=uow_factory,
        resolve=partial(
            runner._resolve_agent_runtime,
            user_id=user_id,
            organization_id=session.conversation.organization_id,
        ),
    )
    ctx = await session.turn_context(
        uow_factory,
        user_id=user_id,
        configured_accounts=runner._resolve_configured_accounts,
    )
    assert session.agent is not None and session.run is not None
    await load_run_log(
        uow_factory,
        conversation=session.conversation,
        run=session.run,
        history=session.history,
    )
    return await specs_for(
        agent=session.agent,
        conversation=session.conversation,
        ctx=ctx,
        surface=ToolSurface.for_harness(resolved.harness_kind),
        uow_factory=runner.tool_units,
        grants=session.grants,
    )


async def test_a_run_start_issues_a_pinned_number_of_statements(pod: TurnPod) -> None:
    conversation = await pod.conversation()
    run_id = UUID((await pod.append(conversation, "hello"))["agent_run_id"])
    user_id = await scalar(
        "SELECT user_id FROM agent_conversations WHERE id = :id",
        id=UUID(conversation),
    )

    with counted_queries() as statements:
        toolsets = await _start_run(
            run_id, user_id=user_id, pod_id=UUID(pod.pod_id), agent_name=pod.agent_name
        )

    assert toolsets
    print(f"run start: {len(statements)} statements")
    assert len(statements) == RUN_START_STATEMENTS, format_statements(
        statements, limit=80
    )
