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

from uuid import UUID

import pytest

from app.modules.agent.services.agent_runner_service import AgentRunnerService
from app.modules.agent.services.harness_wiring import (
    build_harness_registry,
    worker_uow_factory,
)
from app.modules.agent.services.run_context_builder import build_run_context
from app.modules.agent.services.run_context_loader import load_run_context
from app.modules.agent.tests.e2e.turn_work_support import TurnPod, scalar, turn_pod
from app.modules.test_support.query_counting import counted_queries, format_statements

pytestmark = [pytest.mark.e2e]

#: Statements one run start issues on a fresh pod with the POD toolset.
RUN_START_STATEMENTS = 16


@pytest.fixture
async def pod(authenticated_client, fixed_test_org) -> TurnPod:
    return await turn_pod(authenticated_client, fixed_test_org["id"], toolsets=["POD"])


async def _start_run(run_id: UUID, *, user_id: UUID, pod_id: UUID, agent_name: str):
    uow_factory = worker_uow_factory()
    runner = AgentRunnerService(
        uow_factory=uow_factory, harness_registry=build_harness_registry()
    )
    conversation, agent, agent_run, _messages = await load_run_context(
        uow_factory,
        agent_run_id=run_id,
        user_id=user_id,
        pod_id=pod_id,
        agent_name=agent_name,
    )
    resolved = await runner._runtime_for(conversation, agent_run, user_id=user_id)
    ctx = await build_run_context(
        uow_factory=uow_factory,
        conversation=conversation,
        agent=agent,
        agent_run=agent_run,
        user_id=user_id,
        resolved_runtime=resolved,
        runtime_profile_snapshot=resolved.runtime_snapshot().as_payload(),
        resolve_configured_accounts=runner._resolve_configured_accounts,
    )
    return await runner.tool_assembler.assemble(
        agent=agent,
        conversation=conversation,
        vision_mode=ctx.vision_mode,
        grants=ctx.grant_summary,
        host_execution=ctx.host_execution_mode,
        harness_kind=resolved.harness_kind,
        surface_platform=ctx.surface_platform,
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
