"""A function step that fails, routed down its FAILURE edge to a step that
records the failure, against Postgres and the real engine.

Function runs are ended through the workflow's own resume handlers, as the
other workflow e2e tests do; only creating a function needs the sandbox, for
schema extraction.
"""

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from app.core.infrastructure.db.session import async_session_maker
from app.modules.function.infrastructure.models import FunctionRunModel
from app.modules.workflow.tests.e2e.test_flow_execution_e2e import (
    _create_echo_function,
    _create_pod,
    _create_run,
    _create_workflow,
    _drive_function_completed,
    _drive_function_failed,
    _get_run,
)

pytestmark = [pytest.mark.e2e]


def _literals(**values: object) -> dict[str, dict[str, object]]:
    return {key: {"type": "literal", "value": v} for key, v in values.items()}


def _graph(function_name: str, *, failure_edges: int) -> tuple[list, list]:
    nodes = [
        {
            "id": "save",
            "type": "FUNCTION",
            "config": {
                "function_name": function_name,
                "input_mapping": _literals(merchant="Uber", amount=23.5, kind="x"),
            },
        },
        {
            "id": "mark_failed",
            "type": "FUNCTION",
            "config": {
                "function_name": function_name,
                "input_mapping": {
                    **_literals(merchant="Uber", amount=0),
                    # The failure branch reads why the step failed.
                    "kind": {"type": "expression", "value": "save.error"},
                },
            },
        },
        {"id": "end", "type": "END"},
    ]
    edges = [
        {"id": "e1", "source": "save", "target": "end"},
        {"id": "e3", "source": "mark_failed", "target": "end"},
    ]
    edges += [
        {"id": f"f{i}", "source": "save", "target": "mark_failed", "kind": "FAILURE"}
        for i in range(failure_edges)
    ]
    return nodes, edges


async def _function_run_input(function_run_id: str) -> dict:
    async with async_session_maker() as session:
        run = await session.get(FunctionRunModel, UUID(function_run_id))
        assert run is not None
        return dict(run.input_data or {})


@pytest.mark.drives_work
@pytest.mark.asyncio
async def test_a_failed_function_step_runs_its_failure_branch_and_the_run_completes(
    authenticated_client: AsyncClient,
    fixed_test_org,
    configure_workspace_api_url,
):
    _ = configure_workspace_api_url
    client = authenticated_client
    pod_id = await _create_pod(client, fixed_test_org["id"], "Workflow Failure Edge")
    function_name = await _create_echo_function(client, pod_id, function_type="JOB")
    name = f"failure-edge-{uuid4().hex[:6]}"

    # Two places for one failure is refused when the graph is saved.
    nodes, edges = _graph(function_name, failure_edges=2)
    created = await client.post(
        f"/pods/{pod_id}/workflows",
        json={"name": name, "start": {"type": "MANUAL"}, "mode": "GLOBAL"},
    )
    assert created.status_code == 201, created.text
    refused = await client.put(
        f"/pods/{pod_id}/workflows/{name}/graph",
        json={"start": {"type": "MANUAL"}, "nodes": nodes, "edges": edges},
    )
    assert refused.status_code == 422, refused.text
    assert "failure edges; at most 1" in refused.text

    nodes, edges = _graph(function_name, failure_edges=1)
    workflow = await _create_workflow(
        client,
        pod_id,
        name=f"{name}-ok",
        start={"type": "MANUAL"},
        nodes=nodes,
        edges=edges,
    )
    kinds = {edge["id"]: edge["kind"] for edge in workflow["edges"]}
    assert kinds == {"e1": "NEXT", "e3": "NEXT", "f0": "FAILURE"}

    run = await _create_run(client, pod_id, workflow["name"])
    assert run["active_wait"]["node_id"] == "save"
    await _drive_function_failed(
        run["active_wait"]["external_ref"], "function runtime returned 500"
    )

    diverted = await _get_run(client, pod_id, run["id"])
    assert diverted["status"] == "RUNNING", diverted
    assert diverted["error"] is None
    assert diverted["failed_node_id"] is None
    assert diverted["active_wait"]["node_id"] == "mark_failed"
    assert diverted["execution_context"]["save"]["error"] == (
        "function runtime returned 500"
    )
    assert [(s["node_id"], s["status"]) for s in diverted["step_history"]] == [
        ("save", "FAILED"),
        ("mark_failed", "RUNNING"),
    ]
    handler_run = diverted["active_wait"]["external_ref"]
    assert (await _function_run_input(handler_run))["kind"] == (
        "function runtime returned 500"
    )

    await _drive_function_completed(
        handler_run,
        {"merchant": "Uber", "amount": 0, "kind": "failed", "recorded": True},
    )
    completed = await _get_run(client, pod_id, run["id"])
    assert completed["status"] == "COMPLETED"
    assert completed["execution_context"]["mark_failed"]["recorded"] is True
