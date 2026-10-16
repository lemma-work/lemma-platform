"""Retrying a failed run where it failed, and queueing past a run limit.

A parked run's admission happens in the transaction that frees its place
(``test_workflow_work_e2e.py`` races two finishes for it), so there is no lost
admission left for a sweep to find.

Each drives the real engine against Postgres. Agent and function completions
are driven through the workflow's own event handlers, as the other workflow
e2e tests do, so no model or worker is needed; only creating a function needs
the sandbox, for schema extraction.
"""

from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.modules.workflow.tests.e2e.test_flow_execution_e2e import (
    _complete_agent_conversation,
    _create_agent,
    _create_echo_function,
    _create_pod,
    _create_run,
    _create_workflow,
    _drive_function_completed,
    _drive_function_failed,
    _fail_agent_conversation,
    _get_run,
    _submit_form,
)

pytestmark = [pytest.mark.e2e]


async def _retry(client: AsyncClient, pod_id: str, run_id: str, expected: int) -> dict:
    response = await client.post(f"/pods/{pod_id}/workflow-runs/{run_id}/retry")
    assert response.status_code == expected, response.text
    return response.json()


async def _agent_then_function_workflow(client: AsyncClient, pod_id: str) -> str:
    agent_name = await _create_agent(client, pod_id)
    function_name = await _create_echo_function(client, pod_id, function_type="JOB")
    workflow = await _create_workflow(
        client,
        pod_id,
        name=f"retry-{uuid4().hex[:6]}",
        start={"type": "MANUAL"},
        nodes=[
            {
                "id": "draft",
                "type": "AGENT",
                "config": {"agent_name": agent_name, "input_mapping": {}},
            },
            {
                "id": "record",
                "type": "FUNCTION",
                "config": {
                    "function_name": function_name,
                    "input_mapping": {
                        "merchant": {"type": "literal", "value": "Uber"},
                        "amount": {"type": "literal", "value": 23.5},
                        "kind": {"type": "literal", "value": "expense"},
                    },
                },
            },
            {"id": "end", "type": "END"},
        ],
        edges=[
            {"id": "e1", "source": "draft", "target": "record"},
            {"id": "e2", "source": "record", "target": "end"},
        ],
    )
    return workflow["name"]


@pytest.mark.drives_work
@pytest.mark.asyncio
async def test_retry_resumes_at_the_failed_function_without_rerunning_the_agent(
    authenticated_client: AsyncClient,
    fixed_test_org,
    configure_workspace_api_url,
):
    _ = configure_workspace_api_url
    client = authenticated_client
    pod_id = await _create_pod(client, fixed_test_org["id"], "Workflow Retry")
    workflow_name = await _agent_then_function_workflow(client, pod_id)

    run = await _create_run(client, pod_id, workflow_name)
    conversation_id = run["active_wait"]["external_ref"]
    draft = {"items": [{"merchant": "Uber"}]}
    await _complete_agent_conversation(conversation_id, draft)
    run = await _get_run(client, pod_id, run["id"])
    first_function_run = run["active_wait"]["external_ref"]
    await _drive_function_failed(first_function_run, "function runtime returned 500")

    failed = await _get_run(client, pod_id, run["id"])
    assert failed["status"] == "FAILED"
    assert failed["failed_node_id"] == "record"

    retried = await _retry(client, pod_id, run["id"], 200)

    assert retried["status"] == "RUNNING"
    assert retried["error"] is None
    assert retried["active_wait"]["wait_type"] == "FUNCTION"
    second_function_run = retried["active_wait"]["external_ref"]
    assert second_function_run != first_function_run
    # The agent's answer is reused, not asked for again.
    assert retried["execution_context"]["draft"] == draft
    assert [step["node_id"] for step in retried["step_history"]].count("draft") == 1

    await _drive_function_completed(
        second_function_run,
        {"merchant": "Uber", "amount": 23.5, "kind": "expense", "recorded": True},
    )
    completed = await _get_run(client, pod_id, run["id"])
    assert completed["status"] == "COMPLETED"
    assert completed["execution_context"]["draft"] == draft
    assert completed["execution_context"]["record"]["recorded"] is True

    # Only a failed run can be retried.
    await _retry(client, pod_id, run["id"], 409)


@pytest.mark.asyncio
async def test_a_run_that_failed_at_its_agent_is_not_retried(
    authenticated_client: AsyncClient,
    fixed_test_org,
    configure_workspace_api_url,
):
    """Retrying there would pay for the model work again; run it again instead."""
    _ = configure_workspace_api_url
    client = authenticated_client
    pod_id = await _create_pod(client, fixed_test_org["id"], "Workflow No Retry")
    workflow_name = await _agent_then_function_workflow(client, pod_id)

    run = await _create_run(client, pod_id, workflow_name)
    await _fail_agent_conversation(run["active_wait"]["external_ref"], "model down")
    failed = await _get_run(client, pod_id, run["id"])
    assert failed["failed_node_id"] == "draft"

    await _retry(client, pod_id, run["id"], 409)
    assert (await _get_run(client, pod_id, run["id"]))["status"] == "FAILED"


async def _form_workflow(
    client: AsyncClient, pod_id: str, *, limit: int | None
) -> dict:
    workflow = await _create_workflow(
        client,
        pod_id,
        name=f"limited-{uuid4().hex[:6]}",
        start={"type": "MANUAL"},
        nodes=[
            {
                "id": "approve",
                "type": "FORM",
                "config": {
                    "input_schema": {
                        "type": "object",
                        "properties": {"ok": {"type": "boolean"}},
                    }
                },
            },
            {"id": "end", "type": "END"},
        ],
        edges=[{"id": "e1", "source": "approve", "target": "end"}],
    )
    if limit is None:
        return workflow
    patched = await client.patch(
        f"/pods/{pod_id}/workflows/{workflow['name']}",
        json={"max_concurrent_runs": limit},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["max_concurrent_runs"] == limit
    return patched.json()


@pytest.mark.asyncio
async def test_a_run_past_the_limit_waits_its_turn_instead_of_failing(
    authenticated_client: AsyncClient,
    fixed_test_org,
):
    client = authenticated_client
    pod_id = await _create_pod(client, fixed_test_org["id"], "Workflow Limit")
    workflow_name = (await _form_workflow(client, pod_id, limit=1))["name"]

    first = await _create_run(client, pod_id, workflow_name)
    assert first["status"] == "WAITING"
    second = await _create_run(client, pod_id, workflow_name)
    assert second["status"] == "RUNNING"
    assert second["active_wait"]["wait_type"] == "QUEUED"
    assert second["step_history"] == []

    await _submit_form(
        client, pod_id, first["id"], node_id="approve", inputs={"ok": True}
    )

    admitted = await _get_run(client, pod_id, second["id"])
    assert admitted["status"] == "WAITING"
    assert admitted["active_wait"]["wait_type"] == "HUMAN"
    assert admitted["active_wait"]["node_id"] == "approve"
