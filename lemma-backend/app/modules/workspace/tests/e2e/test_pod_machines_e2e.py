"""A pod machine for a factory's QA agent, end to end, on real Docker.

The journey: a pod editor makes a RESTRICTED machine and grants it to one
agent. That agent's commands land on the pod machine -- not on the person's
own -- each run in its own directory; another agent of the same pod is
refused it; and a run a channel started is refused the person's own machine
with the sentence that says what to do instead.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli.models import ExecCommandRequest
from app.modules.agent.tools.workspace_cli.workspace_cli import exec_command_internal
from app.modules.test_support.e2e.waiters import eventually
from sandbox_runtime.paths import RUNS_ROOT

pytestmark = [pytest.mark.e2e, pytest.mark.workspace, pytest.mark.timeout(900)]


async def _agent(client, pod_id: str, name: str) -> dict:
    created = await client.post(
        f"/pods/{pod_id}/agents", json={"name": name, "instruction": "Test."}
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    return created.json()


@pytest.fixture
async def factory(
    local_sandbox_server,
    backend_server,
    configure_workspace_api_url,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    del local_sandbox_server, backend_server, configure_workspace_api_url
    client = authenticated_client
    pod = await client.post(
        "/pods",
        json={
            "name": f"Factory {uuid4().hex[:6]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert pod.status_code == status.HTTP_201_CREATED, pod.text
    pod_id = pod.json()["id"]
    qa = await _agent(client, pod_id, "factory-qa")
    bystander = await _agent(client, pod_id, "bystander")

    machine = await client.post(
        f"/pods/{pod_id}/machines",
        json={"name": "qa", "visibility": "restricted", "run_slots": 2},
    )
    assert machine.status_code == status.HTTP_201_CREATED, machine.text
    granted = await client.put(
        f"/pods/{pod_id}/machines/qa/grants", json={"agents": ["factory-qa"]}
    )
    assert granted.status_code == status.HTTP_200_OK, granted.text

    def context(agent: dict, **extra) -> BaseAgentContext:
        return BaseAgentContext(
            user_id=UUID(fixed_test_user["id"]),
            org_id=UUID(fixed_test_org["id"]),
            pod_id=UUID(pod_id),
            conversation_id=uuid4(),
            agent_name=agent["name"],
            workload_type="agent",
            workload_id=UUID(agent["id"]),
            **extra,
        )

    return {
        "client": client,
        "pod_id": pod_id,
        "machine_id": machine.json()["id"],
        "qa": qa,
        "bystander": bystander,
        "context": context,
    }


async def _exec(ctx: BaseAgentContext, cmd: str):
    return await exec_command_internal(
        ctx, ExecCommandRequest(cmd=cmd, timeout_seconds=180)
    )


async def test_the_granted_agent_runs_on_the_pod_machine_in_its_own_directory(
    factory,
) -> None:
    first = factory["context"](factory["qa"], machine="pod:qa")
    second = factory["context"](factory["qa"], machine="pod:qa")

    ran = await eventually(
        label="the pod machine answering the QA agent",
        probe=lambda: _exec(first, "pwd; cat /etc/hostname"),
        done=lambda result: result.success,
        timeout_seconds=300,
        interval_seconds=2.0,
    )
    other = await _exec(second, "pwd; cat /etc/hostname")
    mine = await _exec(
        factory["context"](factory["qa"]), "cat /etc/hostname"
    )  # no machine: the person's own

    first_dir, first_host = ran.stdout.split()[:2]
    second_dir, second_host = other.stdout.split()[:2]
    assert first_dir == f"{RUNS_ROOT}/factory-qa/{first.conversation_id}"
    assert second_dir == f"{RUNS_ROOT}/factory-qa/{second.conversation_id}"
    assert first_host == second_host, "both runs share the one pod machine"
    assert mine.success and mine.stdout.strip() != first_host


async def test_an_agent_without_the_grant_is_refused(factory) -> None:
    refused = await _exec(
        factory["context"](factory["bystander"], machine="pod:qa"), "true"
    )

    assert not refused.success
    assert "qa" in str(getattr(refused, "error", "") or refused.stderr)


async def test_a_channel_run_is_refused_the_persons_own_machine(factory) -> None:
    channel_run = factory["context"](factory["qa"], started_in_lemma=False)

    refused = await _exec(channel_run, "true")

    assert not refused.success
    said = str(getattr(refused, "error", "") or refused.stderr)
    assert "machine: pod:<name>" in said, said

    # Given the pod machine, the same channel run works.
    on_pod = await _exec(
        factory["context"](factory["qa"], started_in_lemma=False, machine="pod:qa"),
        "true",
    )
    assert on_pod.success, on_pod
