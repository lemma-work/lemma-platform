from __future__ import annotations

import time
from http import HTTPStatus
from uuid import uuid4

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.agentbox]


def test_private_function_executor_supports_sync_schema_and_async_job(
    agentbox_server,
    sandbox_id,
    fake_lemma_function_server,
):
    lemma_base_url, function = fake_lemma_function_server
    manager = agentbox_server.client

    created = manager.request_json(
        "PUT",
        f"/sandboxes/{sandbox_id}",
        body={"env": {"LEMMA_BASE_URL": lemma_base_url}},
        timeout=180,
    )
    assert created.status_code == HTTPStatus.OK, created.text

    headers = {
        "Authorization": f"Bearer {function.token}",
        "X-API-Key": agentbox_server.api_key,
    }
    run_id = str(uuid4())
    execute = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/apps/function_executor/"
        f"pods/{function.pod_id}/functions/{function.name}/execute",
        body={
            "run_id": run_id,
            "input_data": {"text": "agentbox sync"},
            "async_job": False,
            "timeout_seconds": 120,
        },
        headers=headers,
        timeout=180,
    )
    assert execute.status_code == HTTPStatus.OK, execute.text
    result = execute.json()
    assert result["status"] == "completed"
    assert result["output_data"] == {
        "result": "AGENTBOX SYNC",
        "user_id": function.user_id,
        "base_url": lemma_base_url,
    }
    assert result["code_hash"]
    assert any(entry["stream"] == "stdout" for entry in result["logs"])

    schemas = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/apps/function_executor/"
        f"pods/{function.pod_id}/functions/{function.name}/schemas",
        body={"code_hash": result["code_hash"]},
        headers=headers,
        timeout=180,
    )
    assert schemas.status_code == HTTPStatus.OK, schemas.text
    schema_payload = schemas.json()
    assert schema_payload["input_schema"]["title"] == "AgentBoxInput"
    assert schema_payload["output_schema"]["title"] == "AgentBoxOutput"
    assert schema_payload["code_hash"] == result["code_hash"]

    job_run_id = str(uuid4())
    accepted = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/apps/function_executor/"
        f"pods/{function.pod_id}/functions/{function.name}/execute",
        body={
            "run_id": job_run_id,
            "input_data": {"text": "agentbox job"},
            "async_job": True,
            "timeout_seconds": 120,
        },
        headers=headers,
        timeout=180,
    )
    assert accepted.status_code == HTTPStatus.OK, accepted.text
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["run_id"] == job_run_id

    deadline = time.monotonic() + 30
    job_status = None
    while time.monotonic() < deadline:
        job_response = manager.request_json(
            "GET",
            f"/sandboxes/{sandbox_id}/apps/function_executor/runs/{job_run_id}",
            headers={"X-API-Key": agentbox_server.api_key},
            timeout=60,
        )
        assert job_response.status_code == HTTPStatus.OK, job_response.text
        job_status = job_response.json()
        if job_status["status"] == "completed":
            break
        time.sleep(0.5)
    assert job_status is not None
    assert job_status["status"] == "completed"
    assert job_status["output_data"]["result"] == "AGENTBOX JOB"

    logs = manager.request_json(
        "GET",
        f"/sandboxes/{sandbox_id}/apps/function_executor/runs/{job_run_id}/logs",
        headers={"X-API-Key": agentbox_server.api_key},
    )
    assert logs.status_code == HTTPStatus.OK, logs.text
    assert logs.json()["run_id"] == job_run_id
    assert any(entry["stream"] == "stdout" for entry in logs.json()["logs"])

    deleted = manager.request_json(
        "DELETE",
        f"/sandboxes/{sandbox_id}/apps/function_executor/runs/{job_run_id}",
        headers={"X-API-Key": agentbox_server.api_key},
    )
    assert deleted.status_code == HTTPStatus.OK, deleted.text
    assert deleted.json()["deleted"] is True


def test_private_function_executor_requires_function_token(
    agentbox_server,
    sandbox_id,
    fake_lemma_function_server,
):
    lemma_base_url, function = fake_lemma_function_server
    created = agentbox_server.client.request_json(
        "PUT",
        f"/sandboxes/{sandbox_id}",
        body={"env": {"LEMMA_BASE_URL": lemma_base_url}},
        timeout=180,
    )
    assert created.status_code == HTTPStatus.OK, created.text

    response = agentbox_server.client.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/apps/function_executor/"
        f"pods/{function.pod_id}/functions/{function.name}/execute",
        body={
            "run_id": str(uuid4()),
            "input_data": {"text": "missing token"},
            "async_job": False,
        },
        headers={"X-API-Key": agentbox_server.api_key},
        timeout=180,
    )
    assert response.status_code == HTTPStatus.UNAUTHORIZED


def test_private_function_executor_installs_declared_python_package(
    agentbox_server,
    sandbox_id,
    fake_lemma_package_function_server,
):
    """A function declaring `#python_packages: cowsay` and importing it at module
    top level executes only if the executor installed the dependency first."""
    lemma_base_url, function = fake_lemma_package_function_server
    manager = agentbox_server.client

    created = manager.request_json(
        "PUT",
        f"/sandboxes/{sandbox_id}",
        body={"env": {"LEMMA_BASE_URL": lemma_base_url}},
        timeout=180,
    )
    assert created.status_code == HTTPStatus.OK, created.text

    headers = {
        "Authorization": f"Bearer {function.token}",
        "X-API-Key": agentbox_server.api_key,
    }
    execute = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/apps/function_executor/"
        f"pods/{function.pod_id}/functions/{function.name}/execute",
        body={
            "run_id": str(uuid4()),
            "input_data": {"text": "moo"},
            "async_job": False,
            "timeout_seconds": 180,
        },
        headers=headers,
        timeout=240,
    )
    assert execute.status_code == HTTPStatus.OK, execute.text
    result = execute.json()
    assert result["status"] == "completed", result
    assert "moo" in result["output_data"]["rendered"]



# --- run_id idempotency against the real Docker AgentBox ---------------------
#
# A function run is non-idempotent (it can create an Outlook draft, etc.). These
# exercise the real sandbox to prove a re-POSTed run_id never re-runs the body,
# concurrent duplicates collapse to one run, and distinct run_ids each execute.

import concurrent.futures
from uuid import uuid4


def _counter_headers(agentbox_server, sandbox_id, function, lemma_base_url):
    created = agentbox_server.client.request_json(
        "PUT",
        f"/sandboxes/{sandbox_id}",
        body={"env": {"LEMMA_BASE_URL": lemma_base_url}},
        timeout=180,
    )
    assert created.status_code == HTTPStatus.OK, created.text
    return {
        "Authorization": f"Bearer {function.token}",
        "X-API-Key": agentbox_server.api_key,
    }


def _post_execute(manager, sandbox_id, function, headers, *, run_id, marker, async_job=False):
    return manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/apps/function_executor/"
        f"pods/{function.pod_id}/functions/{function.name}/execute",
        body={
            "run_id": run_id,
            "input_data": {"marker": marker},
            "async_job": async_job,
            "timeout_seconds": 120,
        },
        headers=headers,
        timeout=180,
    )


def test_sync_execute_idempotent_on_run_id(
    agentbox_server, sandbox_id, fake_lemma_counter_function_server
):
    lemma_base_url, function = fake_lemma_counter_function_server
    manager = agentbox_server.client
    headers = _counter_headers(agentbox_server, sandbox_id, function, lemma_base_url)
    run_id = str(uuid4())
    marker = uuid4().hex

    first = _post_execute(manager, sandbox_id, function, headers, run_id=run_id, marker=marker)
    # A backend transport-retry re-POSTs the SAME run_id.
    second = _post_execute(manager, sandbox_id, function, headers, run_id=run_id, marker=marker)

    assert first.status_code == HTTPStatus.OK, first.text
    assert second.status_code == HTTPStatus.OK, second.text
    assert first.json()["status"] == "completed"
    # The function body ran exactly once: the side-effect counter stays at 1 and
    # the retry returns the identical cached result.
    assert first.json()["output_data"] == {"invocations": 1}
    assert second.json()["output_data"] == {"invocations": 1}


def test_distinct_run_ids_each_execute(
    agentbox_server, sandbox_id, fake_lemma_counter_function_server
):
    lemma_base_url, function = fake_lemma_counter_function_server
    manager = agentbox_server.client
    headers = _counter_headers(agentbox_server, sandbox_id, function, lemma_base_url)
    marker = uuid4().hex

    first = _post_execute(manager, sandbox_id, function, headers, run_id=str(uuid4()), marker=marker)
    second = _post_execute(manager, sandbox_id, function, headers, run_id=str(uuid4()), marker=marker)

    # Distinct logical runs each execute (idempotency is per run_id, not global).
    assert first.json()["output_data"] == {"invocations": 1}
    assert second.json()["output_data"] == {"invocations": 2}


def test_concurrent_same_run_id_runs_once(
    agentbox_server, sandbox_id, fake_lemma_counter_function_server
):
    lemma_base_url, function = fake_lemma_counter_function_server
    manager = agentbox_server.client
    headers = _counter_headers(agentbox_server, sandbox_id, function, lemma_base_url)
    run_id = str(uuid4())
    marker = uuid4().hex

    # Fire two executes for the SAME run_id concurrently (the backend's retry can
    # race the original). The per-run lock must collapse them to a single run.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_post_execute, manager, sandbox_id, function, headers, run_id=run_id, marker=marker)
            for _ in range(2)
        ]
        responses = [f.result() for f in futures]

    for resp in responses:
        assert resp.status_code == HTTPStatus.OK, resp.text
        assert resp.json()["status"] == "completed"
        assert resp.json()["output_data"] == {"invocations": 1}


def test_async_execute_idempotent_on_run_id(
    agentbox_server, sandbox_id, fake_lemma_counter_function_server
):
    lemma_base_url, function = fake_lemma_counter_function_server
    manager = agentbox_server.client
    headers = _counter_headers(agentbox_server, sandbox_id, function, lemma_base_url)
    run_id = str(uuid4())
    marker = uuid4().hex

    first = _post_execute(manager, sandbox_id, function, headers, run_id=run_id, marker=marker, async_job=True)
    second = _post_execute(manager, sandbox_id, function, headers, run_id=run_id, marker=marker, async_job=True)
    assert first.json()["status"] == "accepted"
    # A re-POST returns the same job, never launching a second run.
    assert second.json()["job_id"] == first.json()["job_id"]

    deadline = time.monotonic() + 30
    status = None
    while time.monotonic() < deadline:
        resp = manager.request_json(
            "GET",
            f"/sandboxes/{sandbox_id}/apps/function_executor/runs/{run_id}",
            headers={"X-API-Key": agentbox_server.api_key},
            timeout=60,
        )
        status = resp.json()
        if status["status"] == "completed":
            break
        time.sleep(0.5)
    assert status is not None and status["status"] == "completed"
    assert status["output_data"] == {"invocations": 1}
