from __future__ import annotations

import sys
import asyncio
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentbox.function_executor import (  # noqa: E402
    FunctionExecuteRequest,
    FunctionExecutor,
    FunctionInvokeResponse,
    FunctionMetadata,
    FunctionSchemaRequest,
    VerifiedToken,
    function_code_hash,
)


FUNCTION_CODE = """#input_type_name: InputModel
#output_type_name: OutputModel
#function_name: run_function
from pydantic import BaseModel

class InputModel(BaseModel):
    x: int

class OutputModel(BaseModel):
    y: int

async def run_function(ctx, data):
    print(f"running {ctx.function_name}")
    return OutputModel(y=data.x + 1)
"""


ENV_FUNCTION_CODE = """#input_type_name: InputModel
#output_type_name: OutputModel
#function_name: run_function
import os
from pydantic import BaseModel

class InputModel(BaseModel):
    x: int

class OutputModel(BaseModel):
    org_id: str

async def run_function(ctx, data):
    assert str(ctx.organization_id) == os.environ.get("LEMMA_ORG_ID")
    return OutputModel(org_id=os.environ.get("LEMMA_ORG_ID", ""))
"""


class _FakeLemmaClient:
    def __init__(self, *, verified: VerifiedToken, metadata: FunctionMetadata):
        self.verified = verified
        self.metadata = metadata
        self.verify_calls = 0
        self.function_calls = 0

    def verify_token(self) -> VerifiedToken:
        self.verify_calls += 1
        return self.verified

    def get_function(self, pod_id, function_name) -> FunctionMetadata:
        self.function_calls += 1
        assert pod_id == self.metadata.pod_id
        assert function_name == self.metadata.name
        return self.metadata


class _TestExecutor(FunctionExecutor):
    def __init__(
        self,
        *,
        client: _FakeLemmaClient,
        workspace_root: str,
        **executor_options,
    ):
        super().__init__(
            workspace_root=workspace_root,
            lemma_base_url="http://lemma.test",
            **executor_options,
        )
        self.client = client

    def api_client(self, token: str):
        assert token == "token"
        return self.client


class _CapacityExecutor(_TestExecutor):
    def __init__(
        self,
        *,
        client: _FakeLemmaClient,
        workspace_root: str,
        max_active: int,
        max_queued: int,
    ):
        super().__init__(client=client, workspace_root=workspace_root)
        self.max_active = max_active
        self.max_queued = max_queued
        self.gate = asyncio.Event()
        self.started = asyncio.Event()
        self.active_invocations = 0
        self.peak_invocations = 0

    async def _execute_isolated(self, handle, **_kwargs):
        self.active_invocations += 1
        self.peak_invocations = max(self.peak_invocations, self.active_invocations)
        self.started.set()
        try:
            await self.gate.wait()
        finally:
            self.active_invocations -= 1
        return FunctionInvokeResponse(
            status="completed",
            output_data={"ok": True},
            code_hash="capacity-test",
            duration_ms=1,
        )


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_function_executor_executes_cached_function(tmp_path):
    pod_id = uuid4()
    function_id = uuid4()
    user_id = uuid4()
    metadata = FunctionMetadata(
        id=function_id,
        name="increment",
        pod_id=pod_id,
        code=FUNCTION_CODE,
        code_hash=function_code_hash(FUNCTION_CODE),
    )
    client = _FakeLemmaClient(
        verified=VerifiedToken(user_id=user_id, email="test@example.com"),
        metadata=metadata,
    )
    executor = _TestExecutor(client=client, workspace_root=str(tmp_path))

    response = await executor.execute(
        pod_id=pod_id,
        function_name="increment",
        request=FunctionExecuteRequest(run_id=uuid4(), input_data={"x": 2}),
        token="token",
    )

    assert response.status == "completed"
    assert response.output_data == {"y": 3}
    assert response.code_hash == metadata.code_hash
    assert response.logs[0].stream == "stdout"
    assert "running increment" in response.logs[0].message
    assert (
        tmp_path
        / "pods"
        / str(pod_id)
        / "functions"
        / "increment"
        / metadata.code_hash
        / "function.py"
    ).exists()


@pytest.mark.anyio
async def test_function_executor_rejects_delegated_function_mismatch(tmp_path):
    pod_id = uuid4()
    metadata = FunctionMetadata(
        id=uuid4(),
        name="increment",
        pod_id=pod_id,
        code=FUNCTION_CODE,
        code_hash=function_code_hash(FUNCTION_CODE),
    )
    client = _FakeLemmaClient(
        verified=VerifiedToken(
            user_id=uuid4(),
            pod_id=pod_id,
            function_name="other_function",
        ),
        metadata=metadata,
    )
    executor = _TestExecutor(client=client, workspace_root=str(tmp_path))

    response = await executor.execute(
        pod_id=pod_id,
        function_name="increment",
        request=FunctionExecuteRequest(run_id=uuid4(), input_data={"x": 2}),
        token="token",
    )

    assert response.status == "failed"
    assert response.error is not None
    assert response.error.name == "HTTPException"


@pytest.mark.anyio
async def test_function_executor_exposes_verified_token_organization_id(tmp_path):
    pod_id = uuid4()
    org_id = uuid4()
    metadata = FunctionMetadata(
        id=uuid4(),
        name="read_env",
        pod_id=pod_id,
        code=ENV_FUNCTION_CODE,
        code_hash=function_code_hash(ENV_FUNCTION_CODE),
    )
    client = _FakeLemmaClient(
        verified=VerifiedToken(user_id=uuid4(), organization_id=org_id),
        metadata=metadata,
    )
    executor = _TestExecutor(client=client, workspace_root=str(tmp_path))

    response = await executor.execute(
        pod_id=pod_id,
        function_name="read_env",
        request=FunctionExecuteRequest(
            run_id=uuid4(),
            input_data={"x": 2},
        ),
        token="token",
    )

    assert response.status == "completed"
    assert response.output_data == {"org_id": str(org_id)}


@pytest.mark.anyio
async def test_function_executor_extracts_schemas(tmp_path):
    pod_id = uuid4()
    metadata = FunctionMetadata(
        id=uuid4(),
        name="increment",
        pod_id=pod_id,
        code=FUNCTION_CODE,
        code_hash=function_code_hash(FUNCTION_CODE),
    )
    client = _FakeLemmaClient(
        verified=VerifiedToken(user_id=uuid4()),
        metadata=metadata,
    )
    executor = _TestExecutor(client=client, workspace_root=str(tmp_path))

    response = await executor.schemas(
        pod_id=pod_id,
        function_name="increment",
        request=FunctionSchemaRequest(code_hash=metadata.code_hash),
        token="token",
    )

    assert response.code_hash == metadata.code_hash
    assert response.input_schema["properties"]["x"]["type"] == "integer"
    assert response.output_schema["properties"]["y"]["type"] == "integer"


@pytest.mark.anyio
async def test_function_executor_runs_async_job_and_exposes_status(tmp_path):
    pod_id = uuid4()
    run_id = uuid4()
    metadata = FunctionMetadata(
        id=uuid4(),
        name="increment",
        pod_id=pod_id,
        code=FUNCTION_CODE,
        code_hash=function_code_hash(FUNCTION_CODE),
    )
    client = _FakeLemmaClient(
        verified=VerifiedToken(user_id=uuid4()),
        metadata=metadata,
    )
    executor = _TestExecutor(client=client, workspace_root=str(tmp_path))

    accepted = await executor.execute(
        pod_id=pod_id,
        function_name="increment",
        request=FunctionExecuteRequest(
            run_id=run_id,
            input_data={"x": 5},
            async_job=True,
        ),
        token="token",
    )

    assert accepted.status == "accepted"
    for _ in range(100):
        status = executor.job_status(run_id)
        if status.status == "completed":
            break
        await asyncio.sleep(0.01)

    status = executor.job_status(run_id)
    logs = executor.job_logs(run_id)
    assert status.status == "completed"
    assert status.output_data == {"y": 6}
    assert logs.logs


# --- declared python package dependencies -----------------------------------

PACKAGE_FUNCTION_CODE = """#input_type_name: InputModel
#output_type_name: OutputModel
#function_name: run_function
#python_packages: cowsay, tabulate
from pydantic import BaseModel

class InputModel(BaseModel):
    x: int

class OutputModel(BaseModel):
    y: int

async def run_function(ctx, data):
    return OutputModel(y=data.x + 1)
"""


def test_parse_python_packages_and_validation():
    from agentbox.function_executor import (
        is_valid_python_package,
        parse_python_packages,
    )

    assert parse_python_packages(PACKAGE_FUNCTION_CODE) == ["cowsay", "tabulate"]
    assert is_valid_python_package("pandas==2.2")
    assert is_valid_python_package("requests[socks,security]")
    assert is_valid_python_package("numpy>=1.0,<2.0")
    assert not is_valid_python_package("--index-url=http://evil")
    assert not is_valid_python_package("foo;bar")
    assert not is_valid_python_package("https://x/y.whl")


def _package_metadata(code: str) -> FunctionMetadata:
    return FunctionMetadata(
        id=uuid4(),
        name="deps",
        pod_id=uuid4(),
        code=code,
        code_hash=function_code_hash(code),
    )


def _executor_for(metadata: FunctionMetadata, tmp_path) -> _TestExecutor:
    client = _FakeLemmaClient(
        verified=VerifiedToken(user_id=uuid4(), email="t@example.com"),
        metadata=metadata,
    )
    return _TestExecutor(client=client, workspace_root=str(tmp_path))


@pytest.mark.anyio
async def test_executor_installs_declared_packages_once(tmp_path, monkeypatch):
    from types import SimpleNamespace

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agentbox.function_executor.subprocess.run", fake_run)
    metadata = _package_metadata(PACKAGE_FUNCTION_CODE)
    executor = _executor_for(metadata, tmp_path)

    first = await executor.execute(
        pod_id=metadata.pod_id,
        function_name="deps",
        request=FunctionExecuteRequest(run_id=uuid4(), input_data={"x": 1}),
        token="token",
    )
    assert first.status == "completed"
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[1:5] == ["-m", "pip", "install", "--target"]
    assert ".dependencies.tmp-" in cmd[5]
    assert "cowsay" in cmd and "tabulate" in cmd

    # Idempotent within the container: a second run does not reinstall.
    second = await executor.execute(
        pod_id=metadata.pod_id,
        function_name="deps",
        request=FunctionExecuteRequest(run_id=uuid4(), input_data={"x": 2}),
        token="token",
    )
    assert second.status == "completed"
    assert len(calls) == 1


@pytest.mark.anyio
async def test_executor_package_install_failure_fails_run(tmp_path, monkeypatch):
    from types import SimpleNamespace

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="ERROR: No matching distribution found for nope-xyz",
        )

    monkeypatch.setattr("agentbox.function_executor.subprocess.run", fake_run)
    code = PACKAGE_FUNCTION_CODE.replace("cowsay, tabulate", "nope-xyz")
    metadata = _package_metadata(code)
    executor = _executor_for(metadata, tmp_path)

    response = await executor.execute(
        pod_id=metadata.pod_id,
        function_name="deps",
        request=FunctionExecuteRequest(run_id=uuid4(), input_data={"x": 1}),
        token="token",
    )
    assert response.status == "failed"
    assert "install" in (response.error.message or "").lower()


@pytest.mark.anyio
async def test_dependency_marker_mismatch_rebuilds_private_dependency_dir(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    calls = 0

    def fake_run(_cmd, **_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agentbox.function_executor.subprocess.run", fake_run)
    metadata = _package_metadata(PACKAGE_FUNCTION_CODE)
    executor = _executor_for(metadata, tmp_path)
    cache_dir, _manifest, _dependency_dir = executor.ensure_cached(metadata)
    assert calls == 1
    (cache_dir / ".dependencies-ready").write_text('["different-package"]')

    executor.ensure_cached(metadata)
    assert calls == 2


@pytest.mark.anyio
async def test_authorization_failure_redacts_invocation_token(tmp_path):
    secret = "CANARY-DELEGATION-TOKEN"
    metadata = _package_metadata(FUNCTION_CODE)
    metadata.name = "increment"

    class _LeakingClient(_FakeLemmaClient):
        def verify_token(self):
            raise RuntimeError(f"upstream rejected bearer {secret}")

    executor = FunctionExecutor(workspace_root=str(tmp_path))
    executor.api_client = lambda _token: _LeakingClient(  # type: ignore[method-assign]
        verified=VerifiedToken(user_id=uuid4()), metadata=metadata
    )
    response = await executor.execute(
        pod_id=metadata.pod_id,
        function_name=metadata.name,
        request=FunctionExecuteRequest(run_id=uuid4(), input_data={"x": 1}),
        token=secret,
    )
    assert response.status == "failed"
    assert secret not in response.model_dump_json()
    assert "[REDACTED]" in response.model_dump_json()


# --- idempotency by run_id --------------------------------------------------

FAILING_FUNCTION_CODE = """#input_type_name: InputModel
#output_type_name: OutputModel
#function_name: run_function
from pydantic import BaseModel

class InputModel(BaseModel):
    x: int

class OutputModel(BaseModel):
    y: int

async def run_function(ctx, data):
    raise ValueError("boom -- side effect already happened")
"""


def _increment_executor(tmp_path, *, code: str = FUNCTION_CODE) -> _TestExecutor:
    metadata = FunctionMetadata(
        id=uuid4(),
        name="increment",
        pod_id=uuid4(),
        code=code,
        code_hash=function_code_hash(code),
    )
    client = _FakeLemmaClient(
        verified=VerifiedToken(user_id=uuid4(), email="t@example.com"),
        metadata=metadata,
    )
    return _TestExecutor(client=client, workspace_root=str(tmp_path))


@pytest.mark.anyio
async def test_sync_execute_is_idempotent_on_run_id(tmp_path):
    executor = _increment_executor(tmp_path)
    pod_id = executor.client.metadata.pod_id
    run_id = uuid4()
    req = FunctionExecuteRequest(run_id=run_id, input_data={"x": 2})

    first = await executor.execute(
        pod_id=pod_id, function_name="increment", request=req, token="token"
    )
    # A re-POST for the same run_id (a backend transport-retry) must NOT re-run.
    second = await executor.execute(
        pod_id=pod_id, function_name="increment", request=req, token="token"
    )

    assert first.status == "completed"
    assert second.output_data == first.output_data == {"y": 3}
    assert second is first  # the cached result is returned verbatim
    assert executor.client.function_calls == 1  # the function body ran once


@pytest.mark.anyio
async def test_different_run_ids_are_not_deduped(tmp_path):
    executor = _increment_executor(tmp_path)
    pod_id = executor.client.metadata.pod_id

    await executor.execute(
        pod_id=pod_id,
        function_name="increment",
        request=FunctionExecuteRequest(run_id=uuid4(), input_data={"x": 2}),
        token="token",
    )
    await executor.execute(
        pod_id=pod_id,
        function_name="increment",
        request=FunctionExecuteRequest(run_id=uuid4(), input_data={"x": 2}),
        token="token",
    )

    # Distinct logical runs each execute (idempotency is per run_id, not global).
    assert executor.client.function_calls == 2


@pytest.mark.anyio
async def test_sync_execute_caches_failed_result(tmp_path):
    # A function that ran its side effect then failed must not be re-run on retry.
    executor = _increment_executor(tmp_path, code=FAILING_FUNCTION_CODE)
    pod_id = executor.client.metadata.pod_id
    run_id = uuid4()
    req = FunctionExecuteRequest(run_id=run_id, input_data={"x": 2})

    first = await executor.execute(
        pod_id=pod_id, function_name="increment", request=req, token="token"
    )
    second = await executor.execute(
        pod_id=pod_id, function_name="increment", request=req, token="token"
    )

    assert first.status == "failed"
    assert second is first
    assert executor.client.function_calls == 1


@pytest.mark.anyio
async def test_sync_run_id_never_reexecutes_after_result_count_eviction(tmp_path):
    executor = _increment_executor(tmp_path)
    pod_id = executor.client.metadata.pod_id
    first_run_id = uuid4()
    first_request = FunctionExecuteRequest(run_id=first_run_id, input_data={"x": 1})
    first = await executor.execute(
        pod_id=pod_id,
        function_name="increment",
        request=first_request,
        token="token",
    )
    for value in range(32):
        await executor.execute(
            pod_id=pod_id,
            function_name="increment",
            request=FunctionExecuteRequest(
                run_id=uuid4(), input_data={"x": value + 10}
            ),
            token="token",
        )
    calls_before_retry = executor.client.function_calls

    with pytest.raises(HTTPException) as exc_info:
        await executor.execute(
            pod_id=pod_id,
            function_name="increment",
            request=first_request,
            token="token",
        )

    assert first.status == "completed"
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "run_result_evicted"
    assert exc_info.value.detail["terminal_status"] == "completed"
    assert exc_info.value.detail["request_fingerprint"]
    assert executor.client.function_calls == calls_before_retry


@pytest.mark.anyio
async def test_sync_run_id_never_reexecutes_after_result_ttl(tmp_path):
    executor = _increment_executor(tmp_path)
    pod_id = executor.client.metadata.pod_id
    run_id = uuid4()
    request = FunctionExecuteRequest(run_id=run_id, input_data={"x": 2})
    await executor.execute(
        pod_id=pod_id,
        function_name="increment",
        request=request,
        token="token",
    )
    _completed_at, response = executor._completed[run_id]
    executor._completed[run_id] = (time.monotonic() - 601, response)
    calls_before_retry = executor.client.function_calls

    with pytest.raises(HTTPException) as exc_info:
        await executor.execute(
            pod_id=pod_id,
            function_name="increment",
            request=request,
            token="token",
        )

    assert exc_info.value.detail["code"] == "run_result_evicted"
    assert executor.client.function_calls == calls_before_retry


@pytest.mark.anyio
async def test_evicted_run_id_preserves_fingerprint_conflict(tmp_path):
    executor = _increment_executor(tmp_path)
    pod_id = executor.client.metadata.pod_id
    run_id = uuid4()
    request = FunctionExecuteRequest(run_id=run_id, input_data={"x": 2})
    await executor.execute(
        pod_id=pod_id,
        function_name="increment",
        request=request,
        token="token",
    )
    _completed_at, response = executor._completed[run_id]
    executor._completed[run_id] = (time.monotonic() - 601, response)
    calls_before_retry = executor.client.function_calls

    with pytest.raises(HTTPException) as exc_info:
        await executor.execute(
            pod_id=pod_id,
            function_name="increment",
            request=FunctionExecuteRequest(run_id=run_id, input_data={"x": 999}),
            token="token",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "run_id_conflict"
    assert executor.client.function_calls == calls_before_retry


@pytest.mark.anyio
async def test_evicted_job_run_id_returns_original_terminal_status(tmp_path):
    executor = _increment_executor(tmp_path)
    pod_id = executor.client.metadata.pod_id
    run_id = uuid4()
    request = FunctionExecuteRequest(run_id=run_id, input_data={"x": 5}, async_job=True)
    await executor.execute(
        pod_id=pod_id,
        function_name="increment",
        request=request,
        token="token",
    )
    for _ in range(100):
        if executor.job_status(run_id).status == "completed":
            break
        await asyncio.sleep(0.01)
    _completed_at, response = executor._completed[run_id]
    executor._completed[run_id] = (time.monotonic() - 601, response)
    executor._sweep_expired_locked(time.monotonic())
    calls_before_retry = executor.client.function_calls

    accepted = await executor.execute(
        pod_id=pod_id,
        function_name="increment",
        request=request,
        token="token",
    )
    status = executor.job_status(run_id)

    assert accepted.status == "accepted"
    assert status.status == "completed"
    assert status.output_data is None
    assert status.error is not None
    assert status.error.name == "ResultNotRetained"
    assert executor.client.function_calls == calls_before_retry


@pytest.mark.anyio
async def test_async_execute_dedups_on_run_id(tmp_path):
    executor = _increment_executor(tmp_path)
    pod_id = executor.client.metadata.pod_id
    run_id = uuid4()
    req = FunctionExecuteRequest(run_id=run_id, input_data={"x": 5}, async_job=True)

    accepted1 = await executor.execute(
        pod_id=pod_id, function_name="increment", request=req, token="token"
    )
    # A re-POST while the job exists must return the same acceptance, not launch
    # a second _run_job.
    accepted2 = await executor.execute(
        pod_id=pod_id, function_name="increment", request=req, token="token"
    )

    assert accepted1.status == "accepted"
    assert accepted2.job_id == accepted1.job_id

    for _ in range(50):
        if executor.job_status(run_id).status == "completed":
            break
        await asyncio.sleep(0.01)

    status = executor.job_status(run_id)
    assert status.status == "completed"
    assert status.output_data == {"y": 6}
    assert executor.client.function_calls == 1  # job body ran once


@pytest.mark.anyio
async def test_executor_rejects_invalid_package_spec(tmp_path, monkeypatch):
    installed = False

    def fake_run(cmd, **kwargs):
        nonlocal installed
        installed = True
        raise AssertionError("pip must not run for an invalid spec")

    monkeypatch.setattr("agentbox.function_executor.subprocess.run", fake_run)
    code = PACKAGE_FUNCTION_CODE.replace("cowsay, tabulate", "--index-url=http://evil")
    metadata = _package_metadata(code)
    executor = _executor_for(metadata, tmp_path)

    response = await executor.execute(
        pod_id=metadata.pod_id,
        function_name="deps",
        request=FunctionExecuteRequest(run_id=uuid4(), input_data={"x": 1}),
        token="token",
    )
    assert response.status == "failed"
    assert installed is False


@pytest.mark.anyio
async def test_sync_and_job_invocations_share_bounded_admission(tmp_path):
    metadata = _package_metadata(FUNCTION_CODE)
    metadata.name = "increment"
    executor = _CapacityExecutor(
        client=_FakeLemmaClient(
            verified=VerifiedToken(user_id=uuid4()), metadata=metadata
        ),
        workspace_root=str(tmp_path),
        max_active=1,
        max_queued=1,
    )
    active = asyncio.create_task(
        executor.execute(
            pod_id=metadata.pod_id,
            function_name=metadata.name,
            request=FunctionExecuteRequest(run_id=uuid4(), input_data={"x": 1}),
            token="token",
        )
    )
    await executor.started.wait()

    queued_id = uuid4()
    accepted = await executor.execute(
        pod_id=metadata.pod_id,
        function_name=metadata.name,
        request=FunctionExecuteRequest(
            run_id=queued_id, input_data={"x": 2}, async_job=True
        ),
        token="token",
    )
    assert accepted.status == "accepted"
    assert executor.job_status(queued_id).status == "queued"

    with pytest.raises(HTTPException) as exc_info:
        await executor.execute(
            pod_id=metadata.pod_id,
            function_name=metadata.name,
            request=FunctionExecuteRequest(run_id=uuid4(), input_data={"x": 3}),
            token="token",
        )
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "1"}

    executor.gate.set()
    assert (await active).status == "completed"
    for _ in range(50):
        if executor.job_status(queued_id).status == "completed":
            break
        await asyncio.sleep(0.01)
    assert executor.job_status(queued_id).status == "completed"
    assert executor.peak_invocations == 1


@pytest.mark.anyio
async def test_queue_wait_counts_against_invocation_timeout(tmp_path):
    metadata = _package_metadata(FUNCTION_CODE)
    metadata.name = "increment"
    executor = _CapacityExecutor(
        client=_FakeLemmaClient(
            verified=VerifiedToken(user_id=uuid4()), metadata=metadata
        ),
        workspace_root=str(tmp_path),
        max_active=1,
        max_queued=1,
    )
    active = asyncio.create_task(
        executor.execute(
            pod_id=metadata.pod_id,
            function_name=metadata.name,
            request=FunctionExecuteRequest(
                run_id=uuid4(), input_data={"x": 1}, timeout_seconds=10
            ),
            token="token",
        )
    )
    await executor.started.wait()
    queued = asyncio.create_task(
        executor.execute(
            pod_id=metadata.pod_id,
            function_name=metadata.name,
            request=FunctionExecuteRequest(
                run_id=uuid4(), input_data={"x": 2}, timeout_seconds=1
            ),
            token="token",
        )
    )
    response = await queued
    assert response.status == "timeout"
    assert executor.peak_invocations == 1
    executor.gate.set()
    await active


SLOW_FUNCTION_CODE = """#input_type_name: InputModel
#output_type_name: OutputModel
#function_name: run_function
import asyncio
from pydantic import BaseModel

class InputModel(BaseModel):
    x: int

class OutputModel(BaseModel):
    y: int

async def run_function(ctx, data):
    await asyncio.sleep(60)
    return OutputModel(y=data.x)
"""


@pytest.mark.anyio
async def test_active_child_cancellation_is_idempotent_and_reports_cancelled(tmp_path):
    executor = _increment_executor(tmp_path, code=SLOW_FUNCTION_CODE)
    run_id = uuid4()
    accepted = await executor.execute(
        pod_id=executor.client.metadata.pod_id,
        function_name="increment",
        request=FunctionExecuteRequest(
            run_id=run_id,
            input_data={"x": 1},
            async_job=True,
            timeout_seconds=120,
        ),
        token="token",
    )
    assert accepted.status == "accepted"
    for _ in range(100):
        handle = executor._runs[run_id]
        if handle.process is not None:
            break
        await asyncio.sleep(0.01)
    assert executor._runs[run_id].process is not None

    first_status = await executor.cancel_status(run_id)
    second_status = await executor.cancel_status(run_id)
    assert first_status.status == "cancelled"
    assert second_status.status == "cancelled"
    assert executor.job_status(run_id).status == "cancelled"


@pytest.mark.anyio
async def test_cancelling_completed_run_preserves_completed_status(tmp_path):
    executor = _increment_executor(tmp_path)
    run_id = uuid4()
    response = await executor.execute(
        pod_id=executor.client.metadata.pod_id,
        function_name="increment",
        request=FunctionExecuteRequest(run_id=run_id, input_data={"x": 1}),
        token="token",
    )
    assert response.status == "completed"
    assert (await executor.cancel_status(run_id)).status == "completed"

    with pytest.raises(HTTPException) as exc_info:
        await executor.cancel_status(uuid4())
    assert exc_info.value.status_code == 404


DESCENDANT_FUNCTION_CODE = r'''#input_type_name: InputModel
#output_type_name: OutputModel
#function_name: run_function
import asyncio
import subprocess
import sys
from pathlib import Path
from pydantic import BaseModel

class InputModel(BaseModel):
    marker: str

class OutputModel(BaseModel):
    ok: bool

async def run_function(ctx, data):
    root = Path(ctx.workspace_root)
    ready = root / f"{data.marker}.child-ready"
    stopped = root / f"{data.marker}.child-stopped"
    child_code = r"""
import os
import signal
import sys
import time
from pathlib import Path

stopped = Path(sys.argv[1])
ready = Path(sys.argv[2])

def terminate(_signum, _frame):
    stopped.write_text("terminated")
    raise SystemExit(0)

signal.signal(signal.SIGTERM, terminate)
ready.write_text(str(os.getpid()))
while True:
    time.sleep(1)
"""
    subprocess.Popen([sys.executable, "-c", child_code, str(stopped), str(ready)])
    while not ready.exists():
        await asyncio.sleep(0.01)
    await asyncio.sleep(60)
    return OutputModel(ok=True)
'''


@pytest.mark.anyio
async def test_cancellation_terminates_worker_descendants(tmp_path):
    executor = _increment_executor(tmp_path, code=DESCENDANT_FUNCTION_CODE)
    marker = uuid4().hex
    run_id = uuid4()
    await executor.execute(
        pod_id=executor.client.metadata.pod_id,
        function_name="increment",
        request=FunctionExecuteRequest(
            run_id=run_id,
            input_data={"marker": marker},
            async_job=True,
            timeout_seconds=120,
        ),
        token="token",
    )
    ready = tmp_path / f"{marker}.child-ready"
    stopped = tmp_path / f"{marker}.child-stopped"
    for _ in range(200):
        if ready.exists():
            break
        await asyncio.sleep(0.01)
    assert ready.exists()

    assert (await executor.cancel_status(run_id)).status == "cancelled"
    for _ in range(200):
        if stopped.exists():
            break
        await asyncio.sleep(0.01)
    assert stopped.read_text() == "terminated"


ENV_ISOLATION_FUNCTION_CODE = """#input_type_name: InputModel
#output_type_name: OutputModel
#function_name: run_function
import os
from pydantic import BaseModel

class InputModel(BaseModel):
    marker: str

class OutputModel(BaseModel):
    marker: str
    inherited_secret: bool

async def run_function(ctx, data):
    print(data.marker)
    return OutputModel(
        marker=data.marker,
        inherited_secret=bool(os.environ.get("CANARY_PARENT_SECRET")),
    )
"""


@pytest.mark.anyio
async def test_workers_isolate_parent_environment_and_invocation_logs(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CANARY_PARENT_SECRET", "must-not-cross-worker-boundary")
    executor = _increment_executor(tmp_path, code=ENV_ISOLATION_FUNCTION_CODE)
    pod_id = executor.client.metadata.pod_id
    first, second = await asyncio.gather(
        executor.execute(
            pod_id=pod_id,
            function_name="increment",
            request=FunctionExecuteRequest(
                run_id=uuid4(), input_data={"marker": "ONLY-FIRST"}
            ),
            token="token",
        ),
        executor.execute(
            pod_id=pod_id,
            function_name="increment",
            request=FunctionExecuteRequest(
                run_id=uuid4(), input_data={"marker": "ONLY-SECOND"}
            ),
            token="token",
        ),
    )
    assert first.output_data == {"marker": "ONLY-FIRST", "inherited_secret": False}
    assert second.output_data == {
        "marker": "ONLY-SECOND",
        "inherited_secret": False,
    }
    assert "ONLY-SECOND" not in "".join(entry.message for entry in first.logs)
    assert "ONLY-FIRST" not in "".join(entry.message for entry in second.logs)


NOISY_FUNCTION_CODE = """#input_type_name: InputModel
#output_type_name: OutputModel
#function_name: run_function
import sys
from pydantic import BaseModel

class InputModel(BaseModel):
    x: int

class OutputModel(BaseModel):
    ok: bool

async def run_function(ctx, data):
    print("O" * 5000)
    print("E" * 5000, file=sys.stderr)
    return OutputModel(ok=True)
"""


LARGE_RESULT_FUNCTION_CODE = """#input_type_name: InputModel
#output_type_name: OutputModel
#function_name: run_function
from pydantic import BaseModel

class InputModel(BaseModel):
    size: int

class OutputModel(BaseModel):
    text: str

async def run_function(ctx, data):
    return OutputModel(text="X" * data.size)
"""


@pytest.mark.anyio
async def test_worker_stdout_and_stderr_are_truncated_at_byte_caps(tmp_path):
    metadata = FunctionMetadata(
        id=uuid4(),
        name="noisy",
        pod_id=uuid4(),
        code=NOISY_FUNCTION_CODE,
        code_hash=function_code_hash(NOISY_FUNCTION_CODE),
    )
    executor = _TestExecutor(
        client=_FakeLemmaClient(
            verified=VerifiedToken(user_id=uuid4()), metadata=metadata
        ),
        workspace_root=str(tmp_path),
        max_stdout_bytes=1024,
        max_stderr_bytes=1024,
    )

    response = await executor.execute(
        pod_id=metadata.pod_id,
        function_name=metadata.name,
        request=FunctionExecuteRequest(run_id=uuid4(), input_data={"x": 1}),
        token="token",
    )

    assert response.status == "completed"
    logs = {entry.stream: entry.message for entry in response.logs}
    assert len(logs["stdout"].encode()) <= 1024
    assert len(logs["stderr"].encode()) <= 1024
    assert "[stdout truncated after 1024 bytes]" in logs["stdout"]
    assert "[stderr truncated after 1024 bytes]" in logs["stderr"]


@pytest.mark.anyio
async def test_worker_rejects_oversized_result_without_losing_capacity(tmp_path):
    metadata = FunctionMetadata(
        id=uuid4(),
        name="large_result",
        pod_id=uuid4(),
        code=LARGE_RESULT_FUNCTION_CODE,
        code_hash=function_code_hash(LARGE_RESULT_FUNCTION_CODE),
    )
    client = _FakeLemmaClient(
        verified=VerifiedToken(user_id=uuid4()), metadata=metadata
    )
    executor = _TestExecutor(
        client=client,
        workspace_root=str(tmp_path),
        max_active=1,
        max_result_bytes=1024,
    )

    oversized = await executor.execute(
        pod_id=metadata.pod_id,
        function_name=metadata.name,
        request=FunctionExecuteRequest(run_id=uuid4(), input_data={"size": 5000}),
        token="token",
    )
    assert oversized.status == "failed"
    assert oversized.error is not None
    assert oversized.error.name == "ResultPayloadTooLargeError"
    assert executor._active == 0

    client.metadata.code = FUNCTION_CODE
    client.metadata.code_hash = function_code_hash(FUNCTION_CODE)
    client.metadata.name = "increment"
    recovered = await executor.execute(
        pod_id=metadata.pod_id,
        function_name="increment",
        request=FunctionExecuteRequest(run_id=uuid4(), input_data={"x": 4}),
        token="token",
    )
    assert recovered.status == "completed"
    assert recovered.output_data == {"y": 5}
    assert executor._active == 0


@pytest.mark.anyio
async def test_parent_terminates_worker_that_exceeds_result_channel_cap(
    tmp_path, monkeypatch
):
    metadata = FunctionMetadata(
        id=uuid4(),
        name="large_result",
        pod_id=uuid4(),
        code=LARGE_RESULT_FUNCTION_CODE,
        code_hash=function_code_hash(LARGE_RESULT_FUNCTION_CODE),
    )
    executor = _TestExecutor(
        client=_FakeLemmaClient(
            verified=VerifiedToken(user_id=uuid4()), metadata=metadata
        ),
        workspace_root=str(tmp_path),
        max_active=1,
        max_result_bytes=1024,
    )
    original_environment = executor._worker_environment

    def worker_environment(result_fd):
        env = original_environment(result_fd)
        env["LEMMA_FUNCTION_MAX_RESULT_BYTES"] = "8192"
        return env

    monkeypatch.setattr(executor, "_worker_environment", worker_environment)
    response = await executor.execute(
        pod_id=metadata.pod_id,
        function_name=metadata.name,
        request=FunctionExecuteRequest(run_id=uuid4(), input_data={"size": 5000}),
        token="token",
    )

    assert response.status == "failed"
    assert response.error is not None
    assert response.error.name == "ResultPayloadTooLargeError"
    assert executor._active == 0
