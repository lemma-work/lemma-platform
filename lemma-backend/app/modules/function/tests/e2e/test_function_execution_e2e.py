"""Function runtime: the queue, concurrency, timeouts, and connectors.

Split from `test_function_e2e.py`, which keeps the shape-and-authorization
half. That file was 200 seconds of the sandbox shard's 310 in one module, and
neither the shard planner (which could only weigh directories) nor xdist (whose
`loadscope` puts a module on one worker) could break it up. These two files
run on separate runners.

The line is what a test is *about*. Everything here is a claim about what
happens when a function actually executes: dispatched through the real streaq
worker, run concurrently, timed out, cancelled, or reaching a connector.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from uuid import uuid4

import pytest
from fastapi import status

from app.modules.test_support.e2e.function_helpers import (
    connector_function_code,
    create_function,
    create_table,
    mcp_function_code,
    patch_connector_operation_execution,
    replace_function_resource_grants,
    replace_role_resource_grants,
    run_function,
    seed_connector_operation,
    seed_user,
    typed_function_code,
    wait_for_run_completion,
)
from app.modules.test_support.e2e.waiters import eventually, wait_for_status

# `usefixtures` is deliberately NOT here. At module scope it made every test
# boot a uvicorn backend server and a local sandbox server, including the ones
# that never supply `code` and only assert status codes -- the duplicate-name
# check measured 18.8s in CI to prove a 409. It is applied per test below, to
# the ones that actually reach the runtime.
#
# `workspace` stays at module scope: it is what the sandbox shard's marker
# filter selects on, and moving it would change which shard these run in. Both
# halves of the split carry it for that reason, and a contract test
# (test_workspace_marked_files_are_routed_consistently_within_a_directory)
# fails if they ever disagree.
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.workspace,
]


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_api_function_concurrent_hot_runs_reports_average_execution_time(
    authenticated_client,
    test_pod,
    worker,
):
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    function_name = f"hot_api_{suffix}"
    total_runs = 20
    concurrency = 5

    code = f"""#input_type_name: HotInput
#output_type_name: HotResult
#function_name: {function_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext

class HotInput(BaseModel):
    value: int

class HotResult(BaseModel):
    value: int
    doubled: int
    caller_user_id: str

async def {function_name}(ctx: FunctionContext, data: HotInput) -> HotResult:
    return HotResult(
        value=data.value,
        doubled=data.value * 2,
        caller_user_id=str(ctx.user_id),
    )"""

    await create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "Hot API concurrency and latency smoke test",
            "type": "API",
            "code": code,
        },
    )

    warm_run = await run_function(
        authenticated_client,
        pod_id,
        function_name,
        {"value": -1},
    )
    assert warm_run["output_data"]["doubled"] == -2

    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(index: int) -> tuple[int, float, dict]:
        async with semaphore:
            started = time.perf_counter()
            final_run = await run_function(
                authenticated_client,
                pod_id,
                function_name,
                {"value": index},
            )
            elapsed = time.perf_counter() - started
            return index, elapsed, final_run

    wall_started = time.perf_counter()
    results = await asyncio.gather(*(run_one(index) for index in range(total_runs)))
    wall_elapsed = time.perf_counter() - wall_started

    durations = [elapsed for _, elapsed, _ in results]
    average_elapsed = sum(durations) / len(durations)
    print(
        "Function API hot concurrency benchmark: "
        f"runs={total_runs} concurrency={concurrency} "
        f"avg={average_elapsed:.3f}s wall={wall_elapsed:.3f}s "
        f"min={min(durations):.3f}s max={max(durations):.3f}s"
    )

    for index, _elapsed, final_run in results:
        output = final_run["output_data"]
        assert output["value"] == index
        assert output["doubled"] == index * 2
        assert output["caller_user_id"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_function_connector_operation_resolves_user_owned_account_in_backend(
    authenticated_client,
    test_pod,
    fixed_test_user,
    db_session,
    worker,
):
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    connector_id = f"dynamic_app_{suffix}"
    function_name = f"dynamic_app_func_{suffix}"
    await seed_connector_operation(
        db_session,
        connector_id=connector_id,
        organization_id=test_pod["organization_id"],
        user_id=fixed_test_user["id"],
        api_key="dynamic-secret",
    )

    function = await create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "Function app operation using dynamic account resolution",
            "code": connector_function_code(
                function_name,
                connector_id,
            ),
        },
    )
    await replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function_name,
        [
            {
                "resource_type": "function",
                "resource_name": function["name"],
                "permission_ids": ["function.read"],
            },
            {
                "resource_type": "connector",
                "resource_name": connector_id,
                "permission_ids": ["connector.use"],
            },
        ],
    )
    await replace_role_resource_grants(
        authenticated_client,
        pod_id,
        "POD_ADMIN",
        [
            {
                "resource_type": "function",
                "resource_name": function["name"],
                "permission_ids": ["function.read"],
            },
            {
                "resource_type": "connector",
                "resource_name": connector_id,
                "permission_ids": ["connector.use"],
            },
        ],
    )

    with patch_connector_operation_execution(connector_id):
        final_run = await run_function(
            authenticated_client,
            pod_id,
            function_name,
            {"message": "hello-dynamic"},
        )

    output = final_run["output_data"]
    assert output["echoed_message"] == "hello-dynamic"
    assert output["used_api_key"] == "dynamic-secret"
    assert output["caller_user_id"] == str(fixed_test_user["id"])


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_function_connector_operation_resolves_agent_owned_account_in_backend(
    authenticated_client,
    test_pod,
    fixed_test_user,
    db_session,
    worker,
):
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    connector_id = f"fixed_app_{suffix}"
    function_name = f"fixed_app_func_{suffix}"
    fixed_account_owner = await seed_user(db_session)
    account = await seed_connector_operation(
        db_session,
        connector_id=connector_id,
        organization_id=test_pod["organization_id"],
        user_id=fixed_account_owner.id,
        api_key="fixed-secret",
    )

    function = await create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "Function app operation using fixed account resolution",
            "code": connector_function_code(
                function_name,
                connector_id,
                account_id=str(account.id),
            ),
        },
    )
    await replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function_name,
        [
            {
                "resource_type": "function",
                "resource_name": function["name"],
                "permission_ids": ["function.read"],
            },
            {
                "resource_type": "connector",
                "resource_name": connector_id,
                "permission_ids": ["connector.use"],
            },
            {
                "resource_type": "connector_account",
                "resource_name": str(account.id),
                "permission_ids": ["connector_account.use"],
            },
        ],
    )
    await replace_role_resource_grants(
        authenticated_client,
        pod_id,
        "POD_ADMIN",
        [
            {
                "resource_type": "function",
                "resource_name": function["name"],
                "permission_ids": ["function.read"],
            },
            {
                "resource_type": "connector",
                "resource_name": connector_id,
                "permission_ids": ["connector.use"],
            },
            {
                "resource_type": "connector_account",
                "resource_name": str(account.id),
                "permission_ids": ["connector_account.use"],
            },
        ],
    )

    with patch_connector_operation_execution(connector_id):
        final_run = await run_function(
            authenticated_client,
            pod_id,
            function_name,
            {"message": "hello-fixed"},
        )

    output = final_run["output_data"]
    assert output["echoed_message"] == "hello-fixed"
    assert output["used_api_key"] == "fixed-secret"
    assert output["caller_user_id"] == str(fixed_test_user["id"])


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_function_connector_operation_fails_when_user_owned_account_missing(
    authenticated_client,
    test_pod,
    fixed_test_user,
    db_session,
    worker,
):
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    connector_id = f"missing_account_app_{suffix}"
    function_name = f"missing_account_func_{suffix}"
    await seed_connector_operation(
        db_session,
        connector_id=connector_id,
        organization_id=test_pod["organization_id"],
    )

    function = await create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "Function app operation missing user account",
            "code": connector_function_code(
                function_name,
                connector_id,
            ),
        },
    )
    await replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function_name,
        [
            {
                "resource_type": "function",
                "resource_name": function["name"],
                "permission_ids": ["function.read"],
            },
            {
                "resource_type": "connector",
                "resource_name": connector_id,
                "permission_ids": ["connector.use"],
            },
        ],
    )
    await replace_role_resource_grants(
        authenticated_client,
        pod_id,
        "POD_ADMIN",
        [
            {
                "resource_type": "function",
                "resource_name": function["name"],
                "permission_ids": ["function.read"],
            },
            {
                "resource_type": "connector",
                "resource_name": connector_id,
                "permission_ids": ["connector.use"],
            },
        ],
    )

    final_run = await run_function(
        authenticated_client,
        pod_id,
        function_name,
        {"message": "hello-missing"},
        expected_status="FAILED",
    )

    assert final_run["error"]
    assert "ACCOUNT_RESOLUTION_ERROR" in final_run["error"]
    assert "Connect your account first" in final_run["error"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_api_function_timeout_marks_run_failed_and_stops_execution(
    authenticated_client,
    test_pod,
    monkeypatch,
):
    from app.core.config import settings as backend_settings

    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    function_name = f"api_timeout_{suffix}"
    table_name = f"timeout_records_{suffix}"

    await create_table(authenticated_client, pod_id, table_name)

    code = f"""#input_type_name: TimeoutInput
#output_type_name: TimeoutResult
#function_name: {function_name}

import asyncio
from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

class TimeoutInput(BaseModel):
    title: str

class TimeoutResult(BaseModel):
    record_id: str

async def {function_name}(ctx: FunctionContext, data: TimeoutInput) -> TimeoutResult:
    await asyncio.sleep(5)
    pod = Pod.from_env()
    record = pod.table("{table_name}").create(
        {{
            "title": data.title,
        }}
    )
    row = record
    return TimeoutResult(record_id=str(row["id"]))"""

    await create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "API function timeout smoke test",
            "type": "API",
            "code": code,
        },
    )

    # Function creation performs schema extraction and prewarms the revision
    # worker. Restrict only the execution whose timeout behavior this test owns.
    monkeypatch.setattr(backend_settings, "function_api_deadline_seconds", 2)
    response = await authenticated_client.post(
        f"/pods/{pod_id}/functions/{function_name}/runs",
        json={"input_data": {"title": "should-not-write"}},
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    run_id = response.json()["id"]

    final_run = await wait_for_run_completion(
        authenticated_client,
        pod_id,
        function_name,
        run_id,
        timeout_seconds=15,
    )
    assert final_run["status"] == "FAILED", final_run
    assert final_run["error"]
    assert "timed out" in final_run["error"].lower()

    await asyncio.sleep(4)

    rerun_response = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{function_name}/runs/{run_id}"
    )
    assert rerun_response.status_code == status.HTTP_200_OK, rerun_response.text
    assert rerun_response.json()["status"] == "FAILED"

    records_response = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/{table_name}/records",
    )
    assert records_response.status_code == status.HTTP_200_OK, records_response.text
    assert records_response.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_job_function_run_completes_via_worker(
    authenticated_client,
    test_pod,
    worker,
):
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    function_name = f"job_func_{suffix}"

    code = f"""#input_type_name: JobInput
#output_type_name: JobResult
#function_name: {function_name}

import asyncio
from pydantic import BaseModel
from lemma_sdk import FunctionContext

class JobInput(BaseModel):
    text: str

class JobResult(BaseModel):
    result: str

async def {function_name}(ctx: FunctionContext, data: JobInput) -> JobResult:
    await asyncio.sleep(1)
    return JobResult(result=data.text.upper())"""

    await create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "Queued function execution smoke test",
            "type": "JOB",
            "code": code,
        },
    )

    response = await authenticated_client.post(
        f"/pods/{pod_id}/functions/{function_name}/runs",
        json={"input_data": {"text": "queued hello"}},
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    run = response.json()
    assert run["status"] in {"PENDING", "RUNNING"}
    assert run["job_id"]

    final_run = await wait_for_run_completion(
        authenticated_client,
        pod_id,
        function_name,
        run["id"],
        timeout_seconds=30,
    )
    assert final_run["status"] == "COMPLETED", final_run
    assert final_run["output_data"]["result"] == "QUEUED HELLO"


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_job_function_execution_writes_datastore_record(
    authenticated_client,
    test_pod,
    worker,
):
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    function_name = f"job_store_{suffix}"
    table_name = f"expenses_{suffix}"

    response = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": table_name,
            "primary_key_column": "id",
            "enable_rls": True,
            "columns": [
                {"name": "title", "type": "TEXT", "required": True},
                {"name": "note", "type": "TEXT", "required": False},
            ],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    table = response.json()

    code = f"""#input_type_name: SaveExpenseInput
#output_type_name: SaveExpenseResult
#function_name: {function_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

class SaveExpenseInput(BaseModel):
    title: str
    note: str

class SaveExpenseResult(BaseModel):
    record_id: str
    caller_user_id: str
    caller_user_email: str | None = None

async def {function_name}(ctx: FunctionContext, data: SaveExpenseInput) -> SaveExpenseResult:
    pod = Pod.from_env()
    record = pod.table("{table_name}").create(
        {{
            "title": data.title,
            "note": data.note,
        }}
    )
    row = record
    return SaveExpenseResult(
        record_id=str(row["id"]),
        caller_user_id=str(ctx.user_id),
        caller_user_email=ctx.user_email,
    )"""

    await create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "Queued datastore write smoke test",
            "type": "JOB",
            "code": code,
        },
    )
    await replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function_name,
        [
            {
                "resource_type": "datastore_table",
                "resource_name": table["name"],
                "permission_ids": [
                    "datastore.table.read",
                    "datastore.record.write",
                ],
            }
        ],
    )
    await replace_role_resource_grants(
        authenticated_client,
        pod_id,
        "POD_ADMIN",
        [
            {
                "resource_type": "datastore_table",
                "resource_name": table["name"],
                "permission_ids": [
                    "datastore.table.read",
                    "datastore.record.read",
                    "datastore.record.write",
                ],
            }
        ],
    )

    final_run = await run_function(
        authenticated_client,
        pod_id,
        function_name,
        {"title": "Taxi", "note": "airport pickup"},
    )
    assert final_run["status"] == "COMPLETED", final_run
    output = final_run["output_data"]
    assert output["caller_user_id"]

    records_response = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/{table_name}/records",
    )
    assert records_response.status_code == status.HTTP_200_OK, records_response.text
    records_payload = records_response.json()
    assert records_payload["total"] == 1
    assert records_payload["items"][0]["id"] == output["record_id"]
    assert records_payload["items"][0]["title"] == "Taxi"
    assert records_payload["items"][0]["note"] == "airport pickup"
    assert records_payload["items"][0]["user_id"] == output["caller_user_id"]


@pytest.mark.slow
@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_job_function_long_run_is_not_destroyed_while_active(
    authenticated_client,
    test_pod,
    worker,
):
    """An active run prevents function-sandbox idle release without heartbeats.

    The idle window is squeezed to five seconds so a sixty-second run is many
    times longer than it, which is what makes the assertion mean something:
    the run finishing proves activity held the sandbox, not that the sweep
    simply never came round.
    """
    from app.modules.workspace.config import workspace_settings

    original_idle = workspace_settings.idle_release_seconds
    workspace_settings.idle_release_seconds = 5
    try:
        pod_id = test_pod["id"]
        suffix = uuid4().hex[:8]
        function_name = f"job_long_{suffix}"
        code = f"""#input_type_name: JobInput
#output_type_name: JobResult
#function_name: {function_name}

import asyncio
from pydantic import BaseModel
from lemma_sdk import FunctionContext

class JobInput(BaseModel):
    seconds: int

class JobResult(BaseModel):
    slept: int

async def {function_name}(ctx: FunctionContext, data: JobInput) -> JobResult:
    await asyncio.sleep(data.seconds)
    return JobResult(slept=data.seconds)"""

        await create_function(
            authenticated_client,
            pod_id,
            {
                "name": function_name,
                "description": "Long-running job: sandbox keepalive smoke test",
                "type": "JOB",
                "code": code,
            },
        )

        response = await authenticated_client.post(
            f"/pods/{pod_id}/functions/{function_name}/runs",
            json={"input_data": {"seconds": 60}},
            follow_redirects=True,
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        run = response.json()

        final_run = await wait_for_run_completion(
            authenticated_client,
            pod_id,
            function_name,
            run["id"],
            timeout_seconds=150,
        )
        assert final_run["status"] == "COMPLETED", final_run
        assert final_run["output_data"]["slept"] == 60
    finally:
        workspace_settings.idle_release_seconds = original_idle


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_concurrent_api_function_runs_all_complete(
    authenticated_client,
    test_pod,
):
    """3-4 API runs fired together (same user -> shared sandbox) all complete.

    Concurrent cold-start callers must coordinate on one sandbox creation
    (Redis creation lock) and then share the RUNNING sandbox; none should fail
    with a sandbox readiness / "Sandbox not found" race.
    """
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    function_name = f"api_concurrent_{suffix}"
    code = f"""#input_type_name: ConcInput
#output_type_name: ConcResult
#function_name: {function_name}

import asyncio
from pydantic import BaseModel
from lemma_sdk import FunctionContext

class ConcInput(BaseModel):
    n: int

class ConcResult(BaseModel):
    doubled: int

async def {function_name}(ctx: FunctionContext, data: ConcInput) -> ConcResult:
    await asyncio.sleep(2)
    return ConcResult(doubled=data.n * 2)"""

    await create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "concurrency smoke (API)",
            "type": "API",
            "code": code,
        },
    )

    async def run_one(n: int) -> dict:
        return await run_function(authenticated_client, pod_id, function_name, {"n": n})

    results = await asyncio.gather(*(run_one(n) for n in range(1, 5)))
    for n, final in zip(range(1, 5), results):
        assert final["status"] == "COMPLETED", final
        assert final["output_data"]["doubled"] == n * 2


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_concurrent_job_function_runs_all_complete(
    authenticated_client,
    test_pod,
    worker,
):
    """3-4 JOB runs fired together (same user -> shared sandbox) all complete."""
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    function_name = f"job_concurrent_{suffix}"
    code = f"""#input_type_name: ConcInput
#output_type_name: ConcResult
#function_name: {function_name}

import asyncio
from pydantic import BaseModel
from lemma_sdk import FunctionContext

class ConcInput(BaseModel):
    n: int

class ConcResult(BaseModel):
    doubled: int

async def {function_name}(ctx: FunctionContext, data: ConcInput) -> ConcResult:
    await asyncio.sleep(3)
    return ConcResult(doubled=data.n * 2)"""

    await create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "concurrency smoke (JOB)",
            "type": "JOB",
            "code": code,
        },
    )

    async def trigger(n: int) -> str:
        resp = await authenticated_client.post(
            f"/pods/{pod_id}/functions/{function_name}/runs",
            json={"input_data": {"n": n}},
            follow_redirects=True,
        )
        assert resp.status_code == status.HTTP_200_OK, resp.text
        return resp.json()["id"]

    run_ids = await asyncio.gather(*(trigger(n) for n in range(1, 5)))
    finals = await asyncio.gather(
        *(
            wait_for_run_completion(
                authenticated_client,
                pod_id,
                function_name,
                rid,
                # Under the shard's `-o timeout=120`, not over it. At 150 this
                # wait could never report: pytest-timeout killed the test at
                # 120 first, so a real stall showed up as a bare SIGKILL with
                # no mention of which run never finished. The four runs are
                # concurrent and measure ~11s, so anything near this is a
                # failure either way -- the only question is whether it says so.
                timeout_seconds=110,
            )
            for rid in run_ids
        )
    )
    for final in finals:
        assert final["status"] == "COMPLETED", final


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_function_runs_a_tenant_connector_operation_for_real(
    authenticated_client,
    test_pod,
    fixed_test_user,
    db_session,
    worker,
    monkeypatch,
):
    """A function calls an MCP server through `pod.connectors.execute`.

    The other connector function tests patch the gateway, so they prove the
    delegated-workload authorization path but stop short of a real call. This
    one runs a live MCP server, installs it as a tenant connector, and has a
    function in the pod runtime execute one of its discovered tools -- so
    nothing between the function and the server is a stand-in.
    """
    import socket
    from uuid import UUID

    from fastmcp import FastMCP

    from app.core.config import settings
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.connectors.api.dependencies import get_connector_service
    from app.modules.connectors.domain.auth_config import AuthConfigSource
    from app.modules.connectors.infrastructure.models.connector import Connector

    monkeypatch.setattr(settings, "connector_allow_private_network_targets", True)

    server = FastMCP("function-runtime")

    @server.tool
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    task = asyncio.create_task(
        server.run_async(
            transport="http", host="127.0.0.1", port=port, show_banner=False
        )
    )

    async def probe_port() -> bool:
        if task.done():
            raise RuntimeError(f"MCP server failed to start: {task.exception()}")
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        await writer.wait_closed()
        return True

    # retry_exceptions=(OSError,): connection refused just means the listener
    # isn't up yet, not a real failure -- same as the original loop's
    # `except OSError: sleep`. A RuntimeError from a dead task still
    # propagates immediately since it isn't in retry_exceptions.
    await eventually(
        label=f"MCP server on 127.0.0.1:{port}",
        probe=probe_port,
        done=lambda ready: ready,
        retry_exceptions=(OSError,),
        timeout_seconds=5,
        interval_seconds=0.05,
    )

    try:
        suffix = uuid4().hex[:8]
        connector_id = f"mcp_fn_{suffix}"
        function_name = f"mcp_fn_func_{suffix}"

        db_session.add(
            Connector(
                id=connector_id,
                title="Function MCP",
                description="MCP server reached from a function.",
                kinds=[
                    {
                        "kind": "mcp",
                        "auth_scheme": "API_KEY",
                        "discovery": "mcp",
                        "auth_config_schema": {
                            "type": "object",
                            "required": ["server_url"],
                            "properties": {"server_url": {"type": "string"}},
                            "additionalProperties": False,
                        },
                    }
                ],
                is_active=True,
            )
        )
        await db_session.commit()

        org_id = UUID(str(test_pod["organization_id"]))
        user_id = UUID(str(fixed_test_user["id"]))
        service = get_connector_service(SqlAlchemyUnitOfWork(db_session))
        install = await service.create_auth_config(
            user_id=user_id,
            organization_id=org_id,
            connector_id=connector_id,
            config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
            config={"server_url": f"http://127.0.0.1:{port}/mcp"},
            name=f"mcp-fn-{suffix}",
        )
        await service.create_account(
            user_id=user_id,
            organization_id=org_id,
            auth_config_id=install.id,
            credentials={"api_key": "unused-by-this-server"},
        )

        function = await create_function(
            authenticated_client,
            test_pod["id"],
            {
                "name": function_name,
                "description": "Runs an MCP tool through the connectors SDK",
                "code": mcp_function_code(function_name, install.name),
            },
        )
        grants = [
            {
                "resource_type": "function",
                "resource_name": function["name"],
                "permission_ids": ["function.read"],
            },
            {
                "resource_type": "connector",
                "resource_name": connector_id,
                "permission_ids": ["connector.use"],
            },
        ]
        await replace_function_resource_grants(
            authenticated_client, test_pod["id"], function_name, grants
        )
        await replace_role_resource_grants(
            authenticated_client, test_pod["id"], "POD_ADMIN", grants
        )

        final_run = await run_function(
            authenticated_client,
            test_pod["id"],
            function_name,
            {"a": 17, "b": 25},
        )
        # 17 + 25, computed by the MCP server, reached from inside the pod
        # runtime through the connectors SDK.
        assert final_run["output_data"]["total"] == 42
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# -- FunctionUseCases sagas with no direct HTTP entry point of their own -----
#
# upsert_function_for_import (bundle import), execute_function_as_workload
# (agent-as-tool) and dispatch_function_for_workflow/cancel_function_run
# (workflow node control) have no HTTP surface at all -- they're built the same
# way their real callers build them (app/modules/agent/tools/
# callable_tool_factory.py, app/composition/workflow_function.py) via
# `build_function_use_cases`, against the live e2e database and the real Docker
# sandbox, same pattern as test_function_sandbox_execution_e2e.py's
# dispatcher-construction tests.
#
# update_function/delete_function are the other half of this group and DO have
# HTTP endpoints, so they stayed in test_function_e2e.py with the rest of the
# shape surface.


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_upsert_function_for_import_creates_then_idempotently_updates(
    authenticated_client, test_pod, fixed_test_user, db_manager
):
    """``upsert_function_for_import`` is the bundle/CLI import saga
    (``app/modules/pod_bundle/infrastructure/function_builder.py::FunctionStepRunner``):
    create-if-absent, update-if-present, by name, with no request object.
    It has no HTTP endpoint of its own, so this builds the same
    ``FunctionUseCases`` the real import path builds."""
    from uuid import UUID

    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.function.api.dependencies import build_function_use_cases
    from app.modules.function.domain.entities import (
        FunctionEntity,
        FunctionStatus,
        FunctionType,
        FunctionUpdateEntity,
    )

    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    func_name = f"import_fn_{uuid4().hex[:8]}"
    use_cases = build_function_use_cases(
        SessionUnitOfWorkFactory(db_manager.session_factory)
    )

    def _entity() -> FunctionEntity:
        return FunctionEntity(
            pod_id=pod_id,
            user_id=user_id,
            name=func_name,
            description="imported",
            type=FunctionType.API,
            visibility="POD",
        )

    first_code = typed_function_code(func_name, expression="data.value + 1")
    created = await use_cases.upsert_function_for_import(
        entity=_entity(),
        update_entity=FunctionUpdateEntity(
            description="imported", code=first_code, type=FunctionType.API
        ),
        code=first_code,
        user_id=user_id,
    )
    assert created.status == FunctionStatus.READY
    first_revision = created.revision_hash
    assert first_revision

    # Applying the bundle again (e.g. `lemma pods import` re-run) must UPDATE
    # the existing row by name, not raise FUNCTION_CONFLICT.
    second_code = typed_function_code(func_name, expression="data.value + 2")
    updated = await use_cases.upsert_function_for_import(
        entity=_entity(),
        update_entity=FunctionUpdateEntity(
            description="imported again", code=second_code, type=FunctionType.API
        ),
        code=second_code,
        user_id=user_id,
    )
    assert updated.id == created.id
    assert updated.description == "imported again"
    assert updated.revision_hash != first_revision

    # Visible + executable through the normal HTTP surface, reflecting the
    # second import's code.
    final_run = await run_function(
        authenticated_client, str(pod_id), func_name, {"value": 10}
    )
    assert final_run["output_data"]["result"] == 12


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_execute_function_as_workload_runs_under_the_agent_delegated_context(
    authenticated_client, test_pod, fixed_test_user, db_manager
):
    """``execute_function_as_workload`` is the agent-as-tool delegated path
    (``app/modules/agent/tools/callable_tool_factory.py``): it authorizes as
    the delegated AGENT principal on behalf of the calling user and runs the
    sandbox with no ctx held. Drive it exactly the way the tool factory does."""
    from uuid import UUID

    from app.core.authorization.permissions import Permissions
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.function.api.dependencies import build_function_use_cases
    from app.modules.function.domain.entities import FunctionRunStatus

    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    func_name = f"workload_fn_{uuid4().hex[:8]}"

    await create_function(
        authenticated_client,
        str(pod_id),
        {
            "name": func_name,
            "description": "workload test",
            "code": typed_function_code(func_name, expression="data.value * 2"),
        },
    )

    # execute_function_as_workload authorizes the AGENT principal against a
    # real per-resource grant (ResourcePermissionGrantModel) -- exactly the
    # grant callable_tool_factory.py relies on when it exposes a function as
    # an agent tool. A bare uuid4() principal has none, so build one for
    # real: create the agent, then grant it function.execute through the
    # agent-permissions endpoint (the resource-access-grant endpoint only
    # accepts ROLE/POD_MEMBER grantees, not AGENT).
    agent_name = f"workload_agent_{uuid4().hex[:8]}"
    agent_response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": agent_name, "instruction": "Answer briefly."},
    )
    assert agent_response.status_code == status.HTTP_201_CREATED, agent_response.text
    agent_id = UUID(agent_response.json()["id"])

    grant_response = await authenticated_client.put(
        f"/pods/{pod_id}/agents/{agent_name}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "function",
                    "resource_name": func_name,
                    "permission_ids": [Permissions.FUNCTION_EXECUTE],
                }
            ]
        },
    )
    assert grant_response.status_code == status.HTTP_200_OK, grant_response.text

    use_cases = build_function_use_cases(
        SessionUnitOfWorkFactory(db_manager.session_factory)
    )
    run = await use_cases.execute_function_as_workload(
        pod_id=pod_id,
        name=func_name,
        input_data={"value": 5},
        user_id=user_id,
        principal_type="AGENT",
        principal_id=agent_id,
        delegation_scope=frozenset([Permissions.FUNCTION_EXECUTE]),
        delegation_actor_name="Test Agent",
    )

    assert run.status == FunctionRunStatus.COMPLETED, run.error
    assert run.output_data == {"result": 10}


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_dispatch_function_for_workflow_enqueues_and_the_worker_completes_it(
    authenticated_client, test_pod, fixed_test_user, db_manager, worker
):
    """``dispatch_function_for_workflow`` is the workflow-node path
    (``app/composition/workflow_function.py::FunctionControlAdapter.execute_function``):
    it forces ASYNCHRONOUS dispatch even for an API function and returns
    PENDING without running the sandbox inline, so the workflow engine can
    suspend on the run id and let the ``FunctionRunCompleted`` event resume
    it. Drive it against the real queue + worker."""
    from uuid import UUID

    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.function.api.dependencies import build_function_use_cases
    from app.modules.function.domain.entities import FunctionRunStatus

    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    func_name = f"workflow_fn_{uuid4().hex[:8]}"

    await create_function(
        authenticated_client,
        str(pod_id),
        {
            "name": func_name,
            "description": "workflow dispatch test",
            "type": "API",
            "code": typed_function_code(func_name, expression="data.value + 1"),
        },
    )

    use_cases = build_function_use_cases(
        SessionUnitOfWorkFactory(db_manager.session_factory)
    )
    dispatched = await use_cases.dispatch_function_for_workflow(
        pod_id=pod_id,
        name=func_name,
        input_data={"value": 41},
        user_id=user_id,
    )

    # PENDING, and NOT run inline even though this is an API function -- the
    # whole point of the workflow path is that the caller never blocks on the
    # sandbox round-trip.
    assert dispatched.status == FunctionRunStatus.PENDING
    assert dispatched.job_id
    assert dispatched.id is not None

    final_run = await wait_for_run_completion(
        authenticated_client,
        str(pod_id),
        func_name,
        str(dispatched.id),
    )
    assert final_run["status"] == "COMPLETED", final_run
    assert final_run["output_data"]["result"] == 42


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_cancel_function_run_stops_a_dispatched_run_before_it_completes(
    authenticated_client, test_pod, worker, db_manager
):
    """``cancel_function_run`` is used when a workflow run that was waiting on
    a dispatched function is itself cancelled
    (``app/composition/workflow_function.py::FunctionControlAdapter.cancel_run``),
    so the sandbox stops doing work nobody is waiting for. A run that is still
    PENDING or RUNNING is cancellable; confirm a long JOB run actually ends
    CANCELLED instead of completing."""
    from uuid import UUID

    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.function.api.dependencies import build_function_use_cases

    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    function_name = f"cancel_job_{suffix}"
    code = f"""#input_type_name: JobInput
#output_type_name: JobResult
#function_name: {function_name}

import asyncio
from pydantic import BaseModel
from lemma_sdk import FunctionContext

class JobInput(BaseModel):
    seconds: int

class JobResult(BaseModel):
    slept: int

async def {function_name}(ctx: FunctionContext, data: JobInput) -> JobResult:
    await asyncio.sleep(data.seconds)
    return JobResult(slept=data.seconds)"""

    await create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "cancel smoke test",
            "type": "JOB",
            "code": code,
        },
    )

    response = await authenticated_client.post(
        f"/pods/{pod_id}/functions/{function_name}/runs",
        json={"input_data": {"seconds": 20}},
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    run = response.json()
    assert run["status"] in {"PENDING", "RUNNING"}

    use_cases = build_function_use_cases(
        SessionUnitOfWorkFactory(db_manager.session_factory)
    )
    # Cancel promptly, well inside the function's 20-second sleep -- a run is
    # cancellable in either PENDING or RUNNING, so there is no race to win.
    await use_cases.cancel_function_run(UUID(run["id"]))

    async def probe() -> dict:
        res = await authenticated_client.get(
            f"/pods/{pod_id}/functions/{function_name}/runs/{run['id']}"
        )
        assert res.status_code == status.HTTP_200_OK, res.text
        return res.json()

    final_run = await wait_for_status(
        label=f"cancelled function run {run['id']}",
        probe=probe,
        expected={"CANCELLED"},
        # A run that finishes or fails instead of cancelling is the actual
        # bug under test -- fail fast rather than waiting out the timeout.
        failed={"COMPLETED", "FAILED"},
        timeout_seconds=15,
        interval_seconds=0.15,
    )
    assert final_run["status"] == "CANCELLED", final_run
