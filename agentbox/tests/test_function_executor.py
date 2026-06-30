from __future__ import annotations

import sys
import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentbox.function_executor import (  # noqa: E402
    FunctionExecuteRequest,
    FunctionExecutor,
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
    def __init__(self, *, client: _FakeLemmaClient, workspace_root: str):
        super().__init__(workspace_root=workspace_root, lemma_base_url="http://lemma.test")
        self.client = client

    def api_client(self, token: str):
        assert token == "token"
        return self.client


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
    for _ in range(20):
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
    from agentbox.function_executor import is_valid_python_package, parse_python_packages

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
    assert cmd[1:5] == ["-m", "pip", "install", "--user"]
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
