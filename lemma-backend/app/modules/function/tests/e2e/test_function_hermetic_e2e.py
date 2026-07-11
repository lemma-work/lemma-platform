"""Required function E2E journeys using the production HTTP/worker boundaries.

The sandbox dependency is the deterministic fake AgentBox HTTP server. Tests
still cross the real API, PostgreSQL, transactional outbox, Redis Streams, and
streaq worker boundaries; only Python execution itself remains in protected CI.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
import pytest
from fastapi import status

pytestmark = pytest.mark.e2e

_CANARY = "CANARY_FUNCTION_SECRET_7f2c9d"


def _function_source(name: str, *, extra: str = "") -> str:
    return f"""#input_type_name: FunctionInput
#output_type_name: FunctionOutput
#function_name: {name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext

class FunctionInput(BaseModel):
    value: str

class FunctionOutput(BaseModel):
    value: str

async def {name}(ctx: FunctionContext, data: FunctionInput) -> FunctionOutput:
    return FunctionOutput(value=data.value)

{extra}
"""


async def _create_function(
    client,
    pod_id: str,
    *,
    function_type: str = "API",
    source: str | None = None,
) -> dict:
    name = f"hermetic_{function_type.lower()}_{uuid4().hex[:8]}"
    response = await client.post(
        f"/pods/{pod_id}/functions",
        json={
            "name": name,
            "description": "Hermetic public-boundary E2E function",
            "type": function_type,
            "code": source or _function_source(name),
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    payload = response.json()
    assert payload["name"] == name
    assert payload["status"] == "READY"
    return payload


async def _configure_executor(e2e_settings, **controls) -> None:
    async with httpx.AsyncClient(base_url=e2e_settings.agentbox_api_url) as client:
        response = await client.post(
            "/__test__/function-executor/configure",
            json=controls,
        )
    assert response.status_code == status.HTTP_200_OK, response.text


async def _executor_state(e2e_settings) -> dict:
    async with httpx.AsyncClient(base_url=e2e_settings.agentbox_api_url) as client:
        response = await client.get("/__test__/function-executor/state")
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()


async def _execute(client, pod_id: str, function_name: str, input_data: dict) -> dict:
    response = await client.post(
        f"/pods/{pod_id}/functions/{function_name}/runs",
        json={"input_data": input_data},
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()


async def _wait_for_terminal_run(
    client,
    pod_id: str,
    function_name: str,
    run_id: str,
    *,
    timeout_seconds: float = 20.0,
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(
            f"/pods/{pod_id}/functions/{function_name}/runs/{run_id}"
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        await asyncio.sleep(0.1)
    raise AssertionError(f"Function run {run_id} did not reach a terminal state")


@pytest.mark.asyncio
async def test_function_definition_lifecycle_permissions_and_validation(
    authenticated_client,
    test_pod,
):
    pod_id = test_pod["id"]
    function = await _create_function(authenticated_client, pod_id)
    name = function["name"]

    detail = await authenticated_client.get(f"/pods/{pod_id}/functions/{name}")
    assert detail.status_code == status.HTTP_200_OK, detail.text
    assert detail.json()["id"] == function["id"]
    assert detail.json()["input_schema"]["type"] == "object"

    listing = await authenticated_client.get(
        f"/pods/{pod_id}/functions", params={"limit": 1}
    )
    assert listing.status_code == status.HTTP_200_OK, listing.text
    assert any(item["name"] == name for item in listing.json()["items"])

    duplicate = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json={"name": name, "code": _function_source(name)},
    )
    assert duplicate.status_code == status.HTTP_409_CONFLICT, duplicate.text
    assert duplicate.json()["code"] == "FUNCTION_CONFLICT"

    updated = await authenticated_client.patch(
        f"/pods/{pod_id}/functions/{name}",
        json={
            "description": "Updated through the public API",
            "code": _function_source(name, extra="# updated schema extraction"),
        },
    )
    assert updated.status_code == status.HTTP_200_OK, updated.text
    assert updated.json()["description"] == "Updated through the public API"
    assert updated.json()["status"] == "READY"

    replaced = await authenticated_client.put(
        f"/pods/{pod_id}/functions/{name}/permissions",
        json={"grants": []},
    )
    assert replaced.status_code == status.HTTP_200_OK, replaced.text
    assert replaced.json() == {
        "function_id": function["id"],
        "function_name": name,
        "grants": [],
    }
    permissions = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{name}/permissions"
    )
    assert permissions.status_code == status.HTTP_200_OK, permissions.text
    assert permissions.json() == replaced.json()

    malformed_name = f"malformed_{uuid4().hex[:8]}"
    malformed = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json={
            "name": malformed_name,
            "code": "def missing_required_lemma_headers():\n    pass\n",
        },
    )
    assert malformed.status_code == status.HTTP_400_BAD_REQUEST, malformed.text
    assert malformed.json()["code"] == "FUNCTION_VALIDATION_ERROR"
    # The durable draft remains available for correction instead of pretending
    # a READY definition was created.
    draft = await authenticated_client.get(f"/pods/{pod_id}/functions/{malformed_name}")
    assert draft.status_code == status.HTTP_200_OK, draft.text
    assert draft.json()["status"] == "DRAFT"

    extraction_error_name = f"schema_error_{uuid4().hex[:8]}"
    extraction_error = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json={
            "name": extraction_error_name,
            "code": _function_source(
                extraction_error_name,
                extra="# LEMMA_TEST_SCHEMA_ERROR",
            ),
        },
    )
    assert extraction_error.status_code == status.HTTP_400_BAD_REQUEST
    assert extraction_error.json()["code"] == "FUNCTION_VALIDATION_ERROR"
    extraction_draft = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{extraction_error_name}"
    )
    assert extraction_draft.status_code == status.HTTP_200_OK
    assert extraction_draft.json()["status"] == "DRAFT"

    deleted = await authenticated_client.delete(f"/pods/{pod_id}/functions/{name}")
    assert deleted.status_code == status.HTTP_200_OK, deleted.text
    missing = await authenticated_client.get(f"/pods/{pod_id}/functions/{name}")
    assert missing.status_code == status.HTTP_404_NOT_FOUND, missing.text


@pytest.mark.asyncio
async def test_api_runs_persist_output_logs_history_and_reuse_hot_session(
    authenticated_client,
    test_pod,
    e2e_settings,
):
    pod_id = test_pod["id"]
    function = await _create_function(authenticated_client, pod_id)
    name = function["name"]
    await _configure_executor(e2e_settings, log_message="public API run completed")

    first = await _execute(authenticated_client, pod_id, name, {"value": "one"})
    second = await _execute(authenticated_client, pod_id, name, {"value": "two"})

    assert first["status"] == "COMPLETED"
    assert first["output_data"] == {
        "echo": {"value": "one"},
        "function": name,
    }
    assert first["logs"] == "public API run completed"
    assert second["status"] == "COMPLETED"
    assert second["workspace_session_id"] == first["workspace_session_id"]

    persisted = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{name}/runs/{first['id']}"
    )
    assert persisted.status_code == status.HTTP_200_OK, persisted.text
    assert persisted.json() == first
    history = await authenticated_client.get(f"/pods/{pod_id}/functions/{name}/runs")
    assert history.status_code == status.HTTP_200_OK, history.text
    assert {item["id"] for item in history.json()["items"]} >= {
        first["id"],
        second["id"],
    }
    assert (await _executor_state(e2e_settings))["invocations"] == 2


@pytest.mark.asyncio
async def test_api_run_redacts_user_failures_and_never_replays_ambiguous_5xx(
    authenticated_client,
    test_pod,
    e2e_settings,
):
    pod_id = test_pod["id"]
    function = await _create_function(authenticated_client, pod_id)
    name = function["name"]

    await _configure_executor(
        e2e_settings,
        modes=["failed"],
        error_message=f"api_key={_CANARY}",
        log_message=f"Authorization: Bearer {_CANARY}",
    )
    failed = await _execute(authenticated_client, pod_id, name, {"value": "fail"})
    serialized = str(failed)
    assert failed["status"] == "FAILED"
    assert _CANARY not in serialized
    assert "[REDACTED]" in serialized

    # A 503 is ambiguous for a synchronous, side-effecting call. The scripted
    # success remains queued, proving production did not submit the call twice.
    await _configure_executor(e2e_settings, modes=["http_503", "success"])
    ambiguous = await _execute(
        authenticated_client,
        pod_id,
        name,
        {"value": "must-run-at-most-once"},
    )
    assert ambiguous["status"] == "FAILED"
    assert "temporarily unavailable" in ambiguous["error"].lower()
    state = await _executor_state(e2e_settings)
    assert state["invocations"] == 1
    assert state["remaining_modes"] == ["success"]

    persisted = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{name}/runs/{ambiguous['id']}"
    )
    assert persisted.status_code == status.HTTP_200_OK, persisted.text
    assert persisted.json()["status"] == "FAILED"


@pytest.mark.asyncio
async def test_api_gateway_timeout_and_malformed_executor_response_are_terminal(
    authenticated_client,
    test_pod,
    e2e_settings,
):
    pod_id = test_pod["id"]
    function = await _create_function(authenticated_client, pod_id)
    name = function["name"]

    await _configure_executor(e2e_settings, modes=["gateway_timeout", "success"])
    timed_out = await _execute(
        authenticated_client,
        pod_id,
        name,
        {"value": "timeout"},
    )
    assert timed_out["status"] == "FAILED"
    assert "timeout" in timed_out["error"].lower()
    assert (await _executor_state(e2e_settings))["invocations"] == 1

    await _configure_executor(e2e_settings, modes=["malformed"])
    malformed = await _execute(
        authenticated_client,
        pod_id,
        name,
        {"value": "malformed"},
    )
    assert malformed["status"] == "FAILED"
    assert (
        malformed["error"] == "The function failed to execute due to an internal error."
    )
    persisted = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{name}/runs/{malformed['id']}"
    )
    assert persisted.status_code == status.HTTP_200_OK, persisted.text
    assert persisted.json()["status"] == "FAILED"


@pytest.mark.asyncio
async def test_job_run_crosses_outbox_stream_worker_and_retries_by_run_id(
    authenticated_client,
    test_pod,
    e2e_settings,
    worker,
):
    del worker  # fixture keeps the real production streaq worker alive
    pod_id = test_pod["id"]
    function = await _create_function(
        authenticated_client,
        pod_id,
        function_type="JOB",
    )
    name = function["name"]
    await _configure_executor(
        e2e_settings,
        modes=["http_503", "success"],
        log_message="queued job completed",
    )

    accepted = await _execute(
        authenticated_client,
        pod_id,
        name,
        {"value": "queued"},
    )
    assert accepted["status"] in {"PENDING", "RUNNING"}
    assert accepted["job_id"]

    final = await _wait_for_terminal_run(
        authenticated_client,
        pod_id,
        name,
        accepted["id"],
    )
    assert final["status"] == "COMPLETED", final
    assert final["output_data"] == {
        "echo": {"value": "queued"},
        "function": name,
    }
    assert final["logs"] == "queued job completed"
    state = await _executor_state(e2e_settings)
    assert state["invocations"] == 2
    assert accepted["id"] in state["runs"]

    history = await authenticated_client.get(f"/pods/{pod_id}/functions/{name}/runs")
    assert history.status_code == status.HTTP_200_OK, history.text
    assert any(item["id"] == accepted["id"] for item in history.json()["items"])
