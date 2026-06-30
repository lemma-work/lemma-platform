"""Unit tests for FunctionService."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionStatus,
    FunctionType,
    FunctionUpdateEntity,
)
from app.modules.function.domain.events import (
    FunctionRunCompletedEvent,
    FunctionRunExecutionRequestedEvent,
    FunctionRunFailedEvent,
)
from app.core.authorization.context import Context
from app.core.domain.errors import DomainError
from app.modules.function.domain.errors import (
    FunctionConflictError,
    FunctionNotFoundError,
    FunctionRunNotFoundError,
    FunctionValidationError,
)
from app.modules.test_support.authz import allow_all_context, deny_all_context
from agentbox_client.apps.function_executor import (
    FunctionInvokeResponse,
    FunctionJobAcceptedResponse,
    FunctionJobStatusResponse,
    FunctionLogsResponse,
    FunctionLogEntry,
    RuntimeErrorInfo,
)
from app.modules.function.services.function_service import FunctionService
from app.modules.function.application.function_run_executor import (
    _API_FUNCTION_TIMEOUT_SECONDS,
    _JOB_FUNCTION_TIMEOUT_SECONDS,
)


pytestmark = pytest.mark.asyncio


async def _execute(service: FunctionService, function, run, *, user_email=None):
    """Run a resolved (function, run) through the bound service's execution
    engine — the production path resolves+creates the run first, then hands the
    entities to this same engine."""
    timeout = (
        _JOB_FUNCTION_TIMEOUT_SECONDS
        if function.type == FunctionType.JOB
        else _API_FUNCTION_TIMEOUT_SECONDS
    )
    return await service._executor.execute(
        function=function, run=run, user_email=user_email, timeout_seconds=timeout
    )


@pytest.fixture(autouse=True)
def _single_attempt_executor_retries(monkeypatch):
    """Default the executor retry budgets to a single attempt so the existing
    single-response tests don't retry (and don't sleep). Retry-specific tests
    override these explicitly. The sandbox machinery (and its retry/recovery
    constants) lives in the execution engine module now."""
    import app.modules.function.application.function_run_executor as ex

    monkeypatch.setattr(ex, "_FUNCTION_EXECUTE_RETRY_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(ex, "_FUNCTION_POLL_RETRY_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(ex, "_SANDBOX_RECOVERY_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(ex, "_SANDBOX_RECOVERY_BACKOFF_SECONDS", 0)


def _function_entity(**overrides) -> FunctionEntity:
    payload = {
        "id": uuid4(),
        "pod_id": uuid4(),
        "user_id": uuid4(),
        "name": "test-function",
        "description": None,
        "input_schema": {},
        "output_schema": {},
        "config_schema": None,
        "config": None,
        "status": FunctionStatus.DRAFT,
        "code_path": None,
    }
    payload.update(overrides)
    return FunctionEntity(**payload)


def _run_entity(**overrides) -> FunctionRunEntity:
    payload = {
        "id": uuid4(),
        "function_id": uuid4(),
        "user_id": uuid4(),
        "status": FunctionRunStatus.PENDING,
        "input_data": {},
        "output_data": None,
        "error": None,
        "logs": None,
    }
    payload.update(overrides)
    return FunctionRunEntity(**payload)


@pytest.fixture
def ctx() -> Context:
    return allow_all_context()


@pytest.fixture
def function_repo() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def run_repo() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def authorization_service() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def workspace_service() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def storage() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def storage_factory(storage: AsyncMock):
    return lambda function_id: storage


@pytest.fixture
def job_queue() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def service(
    function_repo: AsyncMock,
    run_repo: AsyncMock,
    authorization_service: AsyncMock,
    workspace_service: AsyncMock,
    storage_factory,
    job_queue: AsyncMock,
) -> FunctionService:
    return FunctionService(
        function_repository=function_repo,
        run_repository=run_repo,
        workspace_service=workspace_service,
        storage_factory=storage_factory,
        job_queue=job_queue,
        authorization_service=authorization_service,
    )


async def test_create_function_success_without_code(
    service: FunctionService,
    function_repo: AsyncMock,
    ctx: Context,
):
    entity = _function_entity(id=None, name="f1")
    function_repo.get_by_name.return_value = None
    created = _function_entity(name="f1")
    function_repo.create.return_value = created

    result = await service.create_function(entity, created.user_id, ctx=ctx)

    assert result == created
    function_repo.create.assert_awaited_once()


async def test_create_function_conflict(
    service: FunctionService,
    function_repo: AsyncMock,
    ctx: Context,
):
    entity = _function_entity(id=None, name="dupe")
    function_repo.get_by_name.return_value = _function_entity(name="dupe")

    with pytest.raises(FunctionConflictError):
        await service.create_function(entity, entity.user_id, ctx=ctx)


async def test_create_function_permission_denied(
    service: FunctionService,
    function_repo: AsyncMock,
):
    entity = _function_entity(id=None)
    denying_ctx = deny_all_context(user_id=entity.user_id, pod_id=entity.pod_id)

    with pytest.raises(DomainError) as exc_info:
        await service.create_function(entity, entity.user_id, ctx=denying_ctx)

    assert exc_info.value.status_code == 403
    function_repo.create.assert_not_awaited()


async def test_create_function_with_code_updates_schemas(
    service: FunctionService,
    function_repo: AsyncMock,
    storage: AsyncMock,
    ctx: Context,
):
    entity = _function_entity(id=None, name="with-code")
    created = _function_entity(name="with-code", id=uuid4())
    updated = created.model_copy()
    updated.status = FunctionStatus.READY
    function_repo.get_by_name.return_value = None
    function_repo.create.return_value = created
    function_repo.update.return_value = updated
    service._extract_schemas = AsyncMock(return_value=({"a": 1}, {"b": 2}, None))

    result = await service.create_function(entity, created.user_id, code="# code", ctx=ctx)

    assert result.status == FunctionStatus.READY
    storage.write_file.assert_awaited_once()
    function_repo.update.assert_awaited_once()


async def test_parse_python_packages_extracts_and_dedupes(service: FunctionService):
    code = (
        "#input_type_name: In\n"
        "#output_type_name: Out\n"
        "#function_name: f\n"
        "#python_packages: pandas, numpy pandas requests[socks] numpy>=1.0,<2.0\n"
        "\nimport pandas\n"
    )
    assert service._parse_python_packages(code) == [
        "pandas",
        "numpy",
        "requests[socks]",
        "numpy>=1.0,<2.0",
    ]


async def test_parse_python_packages_empty_when_absent(service: FunctionService):
    code = "#input_type_name: In\n#output_type_name: Out\n#function_name: f\n\npass\n"
    assert service._parse_python_packages(code) == []


@pytest.mark.parametrize(
    "bad",
    ["--index-url=http://evil", "foo;rm -rf /", "https://x/y.whl", "../pkg", "a b&c"],
)
async def test_parse_python_packages_rejects_unsafe(service: FunctionService, bad: str):
    code = f"#function_name: f\n#python_packages: {bad}\n\npass\n"
    with pytest.raises(FunctionValidationError):
        service._parse_python_packages(code)


async def test_create_function_stores_python_packages(
    service: FunctionService,
    function_repo: AsyncMock,
    ctx: Context,
):
    entity = _function_entity(id=None, name="with-deps")
    created = _function_entity(name="with-deps", id=uuid4())
    function_repo.get_by_name.return_value = None
    function_repo.create.return_value = created
    function_repo.update.return_value = created
    service._extract_schemas = AsyncMock(return_value=({}, {}, None))

    code = (
        "#input_type_name: In\n"
        "#output_type_name: Out\n"
        "#function_name: f\n"
        "#python_packages: cowsay\n"
        "\nimport cowsay\n"
    )
    await service.create_function(entity, created.user_id, code=code, ctx=ctx)
    assert created.python_packages == ["cowsay"]


async def test_get_function_by_name_not_found_strict(service: FunctionService, function_repo: AsyncMock):
    function_repo.get_by_name.return_value = None
    with pytest.raises(FunctionNotFoundError):
        await service.get_function_by_name(uuid4(), "missing", uuid4(), raise_not_found=True)


async def test_update_function_not_found(
    service: FunctionService,
    function_repo: AsyncMock,
):
    function_repo.get_by_name.return_value = None
    with pytest.raises(FunctionNotFoundError):
        await service.update_function(uuid4(), "x", FunctionUpdateEntity(), uuid4())


async def test_update_function_null_config_preserves_existing_config(
    service: FunctionService,
    function_repo: AsyncMock,
    ctx: Context,
):
    function = _function_entity(config={"api_key": "live-secret", "api_url": "https://example.com"})
    function_repo.get_by_name.return_value = function
    function_repo.update.side_effect = lambda entity: entity

    result = await service.update_function(
        function.pod_id,
        function.name,
        FunctionUpdateEntity(config=None),
        function.user_id,
        ctx=ctx,
    )

    assert result.config == {"api_key": "live-secret", "api_url": "https://example.com"}


async def test_update_function_empty_config_resets_existing_config(
    service: FunctionService,
    function_repo: AsyncMock,
    ctx: Context,
):
    function = _function_entity(config={"api_key": "live-secret", "api_url": "https://example.com"})
    function_repo.get_by_name.return_value = function
    function_repo.update.side_effect = lambda entity: entity

    result = await service.update_function(
        function.pod_id,
        function.name,
        FunctionUpdateEntity(config={}),
        function.user_id,
        ctx=ctx,
    )

    assert result.config == {}


async def test_delete_function_success(
    service: FunctionService,
    function_repo: AsyncMock,
    ctx: Context,
):
    function = _function_entity()
    function_repo.get_by_name.return_value = function
    function_repo.delete.return_value = True

    result = await service.delete_function(
        function.pod_id, function.name, function.user_id, ctx=ctx
    )

    assert result is True
    function_repo.delete.assert_awaited_once_with(function.id)


async def test_list_runs_for_function(
    service: FunctionService,
    function_repo: AsyncMock,
    run_repo: AsyncMock,
    ctx: Context,
):
    function = _function_entity()
    function_repo.get_by_name.return_value = function
    run = _run_entity(function_id=function.id, user_id=function.user_id, input_data={"a": 1})
    run_repo.list_runs_by_function.return_value = ([run], None)

    runs, cursor = await service.list_runs(
        function.pod_id, function.name, function.user_id, limit=10, ctx=ctx
    )
    assert len(runs) == 1
    assert cursor is None



async def test_get_run_not_found(service: FunctionService, run_repo: AsyncMock):
    run_repo.get_run.return_value = None
    with pytest.raises(FunctionRunNotFoundError):
        await service.get_run(uuid4(), "f", uuid4(), uuid4())


async def test_get_run_function_mismatch(
    service: FunctionService,
    run_repo: AsyncMock,
    function_repo: AsyncMock,
    ctx: Context,
):
    function = _function_entity()
    run_repo.get_run.return_value = _run_entity(function_id=uuid4(), user_id=function.user_id)
    function_repo.get_by_name.return_value = function

    with pytest.raises(FunctionValidationError, match="does not belong"):
        await service.get_run(
            function.pod_id, function.name, uuid4(), function.user_id, ctx=ctx
        )


class _FakeSession:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.session_id = "workspace-session-1"
        self.sandbox_id = "sandbox-1"
        self.env_vars = {
            "LEMMA_TOKEN": "lemma-token",
        }
        self.exec_calls = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def close(self):
        self.closed = True

    async def execute_code(self, _code, timeout=None):
        del timeout
        return next(self._responses)

    async def exec_command(self, **kwargs):
        self.exec_calls.append(kwargs)
        response = next(self._responses)
        if isinstance(response, dict):
            return response
        status = "ok" if response.success else "error"
        error = response.error_in_exec if not response.success else None
        return {
            "completed": True,
            "success": response.success,
            "stdout": (
                (response.stdout or "")
                + '__LEMMA_FUNCTION_RUNNER__={"status":'
                + f'"{status}","error":'
                + json.dumps(error)
                + "}\n"
            ),
            "stderr": response.stderr or "",
            "error": None,
        }


class _RaisingExecuteCodeSession:
    session_id = "workspace-session-raising"
    sandbox_id = "sandbox-raising"
    env_vars = {"LEMMA_TOKEN": "lemma-token"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def close(self):
        return None

    async def execute_code(self, _code, timeout=None):
        del _code, timeout
        raise self._error()

    async def exec_command(self, **kwargs):
        del kwargs
        raise self._error()

    def _error(self):
        request = httpx.Request(
            "POST",
            "https://agentbox.test/sandboxes/s1/sessions/workspace-session-raising/python",
        )
        response = httpx.Response(
            500,
            request=request,
            text='{"detail":"sandbox execution crashed"}',
        )
        return httpx.HTTPStatusError("server error", request=request, response=response)


class _FakeFunctionExecutorClient:
    def __init__(self, responses):
        self._responses = responses
        self.execute_calls = []
        self.status_calls = []
        self.log_calls = []
        self.ready_calls = []
        self.event_order = []
        self.closed = False

    async def wait_until_ready(self, **kwargs):
        self.ready_calls.append(kwargs)
        self.event_order.append("ready")

    async def health(self, **kwargs):
        return True

    async def execute(self, **kwargs):
        self.execute_calls.append(kwargs)
        self.event_order.append("execute")
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return response

    async def get_status(self, **kwargs):
        self.status_calls.append(kwargs)
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return response

    async def get_logs(self, **kwargs):
        self.log_calls.append(kwargs)
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self):
        self.closed = True


def _install_executor(service: FunctionService, responses):
    clients: list[_FakeFunctionExecutorClient] = []
    response_iter = iter(responses)

    def _factory(token: str):
        assert token == "lemma-token"
        client = _FakeFunctionExecutorClient(response_iter)
        clients.append(client)
        return client

    service.function_executor_client_factory = _factory
    # The execution engine (which actually builds the client) captured the factory
    # at construction, so set it there too.
    service._executor.function_executor_client_factory = _factory
    return clients


async def test_execute_function_failure_updates_run(
    service: FunctionService,
    function_repo: AsyncMock,
    run_repo: AsyncMock,
    workspace_service: AsyncMock,
    ctx: Context,
):
    function = _function_entity(code_path="f.py")
    function_repo.get_by_name.return_value = function
    run = _run_entity(function_id=function.id, user_id=function.user_id, input_data={"a": 1})
    run_repo.create_run.return_value = run

    workspace_service.get_session.return_value = _FakeSession([])
    _install_executor(
        service,
        [
            FunctionInvokeResponse(
                status="failed",
                error=RuntimeErrorInfo(name="RuntimeError", message="bad load"),
                logs=[],
                code_hash="abc",
                duration_ms=10,
            )
        ],
    )

    result = await _execute(service, function, run)

    assert result.status == FunctionRunStatus.FAILED
    assert result.error == "bad load"
    # PENDING->RUNNING + workspace-session-id updates stay on update_run; the
    # terminal FAILED transition now goes through update_run_and_collect so its
    # completion/failure event is emitted.
    assert run_repo.update_run.await_count == 2
    assert run_repo.update_run_and_collect.await_count == 1


async def test_execute_function_api_uses_cached_workspace_command(
    service: FunctionService,
    function_repo: AsyncMock,
    run_repo: AsyncMock,
    workspace_service: AsyncMock,
    ctx: Context,
):
    function = _function_entity(code_path="f.py", code_hash="abc123")
    function_repo.get_by_name.return_value = function
    run = _run_entity(
        function_id=function.id,
        user_id=function.user_id,
        input_data={"a": 1},
    )
    run_repo.create_run.return_value = run
    service._get_code = AsyncMock()
    fake_session = _FakeSession([])
    workspace_service.get_session.return_value = fake_session
    clients = _install_executor(
        service,
        [
            FunctionInvokeResponse(
                status="completed",
                output_data={"done": True},
                logs=[
                    FunctionLogEntry(
                        timestamp="2026-06-02T00:00:00Z",
                        stream="stdout",
                        message="ok",
                    )
                ],
                code_hash="abc123",
                duration_ms=10,
            )
        ],
    )

    result = await _execute(service, function, run)

    assert result.status == FunctionRunStatus.COMPLETED
    assert result.output_data == {"done": True}
    workspace_kwargs = workspace_service.get_session.await_args.kwargs
    assert workspace_kwargs["session_id"] == f"function-api-{function.id}"
    assert workspace_kwargs["initial_cwd"] == service._executor._api_workspace_cwd(function)
    assert workspace_kwargs["close_on_exit"] is False
    assert not fake_session.exec_calls
    execute_call = clients[0].execute_calls[-1]
    assert execute_call["sandbox_id"] == "sandbox-1"
    assert execute_call["pod_id"] == function.pod_id
    assert execute_call["function_name"] == function.name
    assert execute_call["request"].input_data == {"a": 1}
    assert not hasattr(execute_call["request"], "env_vars")
    assert execute_call["request"].async_job is False
    service._get_code.assert_not_awaited()


async def test_execute_function_api_cache_hit_skips_code_fetch(
    service: FunctionService,
    function_repo: AsyncMock,
    run_repo: AsyncMock,
    workspace_service: AsyncMock,
    ctx: Context,
):
    function = _function_entity(code_path="f.py")
    function_repo.get_by_name.return_value = function
    run = _run_entity(function_id=function.id, user_id=function.user_id)
    run_repo.create_run.return_value = run
    service._get_code = AsyncMock()

    fake_session = _FakeSession([])
    workspace_service.get_session.return_value = fake_session
    _install_executor(
        service,
        [
            FunctionInvokeResponse(
                status="completed",
                output_data={"cached": True},
                logs=[],
                code_hash="abc123",
                duration_ms=10,
            )
        ],
    )

    result = await _execute(service, function, run)

    assert result.status == FunctionRunStatus.COMPLETED
    assert result.output_data == {"cached": True}
    service._get_code.assert_not_awaited()
    assert fake_session.exec_calls == []


async def test_execute_function_sandbox_http_error_surfaces_clean_error(
    service: FunctionService,
    function_repo: AsyncMock,
    run_repo: AsyncMock,
    workspace_service: AsyncMock,
    ctx: Context,
):
    function = _function_entity(code_path="f.py")
    function_repo.get_by_name.return_value = function
    run = _run_entity(function_id=function.id, user_id=function.user_id)
    run_repo.create_run.return_value = run
    workspace_service.get_session.return_value = _FakeSession([])
    request = httpx.Request("POST", "https://agentbox.test/function-executor")
    response = httpx.Response(
        500,
        request=request,
        text='{"detail":"sandbox execution crashed"}',
    )
    _install_executor(
        service,
        [httpx.HTTPStatusError("server error", request=request, response=response)],
    )

    result = await _execute(service, function, run)

    assert result.status == FunctionRunStatus.FAILED
    # Fix 3: the run carries a clean, user-facing error — never the HTTP status
    # code or the raw sandbox/manager response body.
    assert "unexpected error" in result.error.lower()
    assert "HTTP 500" not in result.error
    assert "sandbox execution crashed" not in result.error
    assert "sandbox execution crashed" not in (result.logs or "")
    final_update = run_repo.update_run_and_collect.await_args.kwargs
    assert final_update["status"] == FunctionRunStatus.FAILED
    assert "sandbox execution crashed" not in (final_update.get("logs") or "")


async def test_execute_function_recovers_from_transient_sandbox_error(
    service: FunctionService,
    function_repo: AsyncMock,
    run_repo: AsyncMock,
    workspace_service: AsyncMock,
    ctx: Context,
    monkeypatch,
):
    """A recoverable sandbox error (e.g. the pod vanished mid-run -> 404) must
    reprovision the sandbox and re-run, not fail. The run still completes and a
    second execution attempt is made against a freshly acquired session."""
    import app.modules.function.application.function_run_executor as fs

    monkeypatch.setattr(fs, "_SANDBOX_RECOVERY_MAX_ATTEMPTS", 2)

    function = _function_entity(code_path="f.py")
    function_repo.get_by_name.return_value = function
    run = _run_entity(function_id=function.id, user_id=function.user_id)
    run_repo.create_run.return_value = run
    workspace_service.get_session.return_value = _FakeSession([])

    request = httpx.Request(
        "GET",
        "https://agentbox.test/sandboxes/x/apps/function_executor/runs/y",
    )
    sandbox_gone = httpx.HTTPStatusError(
        "not found",
        request=request,
        response=httpx.Response(
            404, request=request, text='{"detail":"Sandbox not found"}'
        ),
    )
    clients = _install_executor(
        service,
        [
            sandbox_gone,
            FunctionInvokeResponse(
                status="completed",
                output_data={"ok": True},
                logs=[],
                code_hash="abc",
                duration_ms=5,
            ),
        ],
    )

    result = await _execute(service, function, run)

    assert result.status == FunctionRunStatus.COMPLETED, result.error
    assert result.output_data == {"ok": True}
    # Reprovisioned (get_session re-acquired) and executed a second time.
    assert workspace_service.get_session.await_count == 2
    assert sum(len(c.execute_calls) for c in clients) == 2


async def test_execute_function_job_adds_execution_requested_event_and_returns_pending_run(
    service: FunctionService,
    function_repo: AsyncMock,
    run_repo: AsyncMock,
    ctx: Context,
):
    function = _function_entity(code_path="f.py", type=FunctionType.JOB)
    function_repo.get_by_name.return_value = function
    run = _run_entity(function_id=function.id, user_id=function.user_id)
    run_repo.create_run.return_value = run

    resolved = await service.resolve_execute(
        function.pod_id,
        function.name,
        {"a": 1},
        function.user_id,
        "user@example.com",
        ctx=ctx,
    )

    created_run = run_repo.create_run.await_args.args[0]
    events = created_run.collect_events()

    assert resolved.run.status == FunctionRunStatus.PENDING
    assert len(events) == 1
    assert isinstance(events[0], FunctionRunExecutionRequestedEvent)
    assert events[0].function_id == function.id
    assert events[0].run_id == created_run.id


async def test_execute_run_by_id_job_command_failure_returns_failed_run(
    service: FunctionService,
    function_repo: AsyncMock,
    run_repo: AsyncMock,
    workspace_service: AsyncMock,
):
    function = _function_entity(code_path="f.py", type=FunctionType.JOB)
    run = _run_entity(
        function_id=function.id,
        user_id=function.user_id,
        status=FunctionRunStatus.PENDING,
    )
    function_repo.get.return_value = function
    run_repo.get_run.return_value = run

    async def _update_run(run_id, **kwargs):
        assert run_id == run.id
        return run.model_copy(update=kwargs)

    run_repo.update_run.side_effect = _update_run
    workspace_service.get_session.return_value = _FakeSession([])
    _install_executor(
        service,
        [
            FunctionInvokeResponse(
                status="failed",
                error=RuntimeErrorInfo(name="ExecutorError", message="AgentBox returned 500"),
                logs=[
                    FunctionLogEntry(
                        timestamp="2026-06-02T00:00:00Z",
                        stream="stdout",
                        message="boot log\nstartup stderr\n",
                    )
                ],
                code_hash="abc",
                duration_ms=10,
            )
        ],
    )

    result = await _execute(service, function, run, user_email=run.user_email)

    assert result.status == FunctionRunStatus.FAILED
    assert result.error == "AgentBox returned 500"
    assert "boot log" in result.logs
    assert "startup stderr" in result.logs


async def test_execute_run_by_id_for_job_uses_one_shot_workspace_command(
    service: FunctionService,
    function_repo: AsyncMock,
    run_repo: AsyncMock,
    workspace_service: AsyncMock,
):
    function = _function_entity(code_path="f.py", type=FunctionType.JOB)
    run = _run_entity(function_id=function.id, user_id=function.user_id, status=FunctionRunStatus.PENDING)
    function_repo.get.return_value = function
    run_repo.get_run.return_value = run
    running_run = run.model_copy(
        update={
            "status": FunctionRunStatus.RUNNING,
            "workspace_session_id": "workspace-session-job-1",
            "workspace_process_id": None,
        }
    )
    completed_run = running_run.model_copy(update={"status": FunctionRunStatus.COMPLETED})
    run_repo.update_run.side_effect = [running_run, completed_run]

    session = _FakeSession([])
    workspace_service.get_session.return_value = session
    clients = _install_executor(
        service,
        [
            FunctionJobAcceptedResponse(run_id=run.id, job_id=f"function:{run.id}"),
            FunctionJobStatusResponse(
                run_id=run.id,
                job_id=f"function:{run.id}",
                status="completed",
                output_data={"done": True},
                code_hash="abc",
                duration_ms=10,
            ),
            FunctionLogsResponse(
                run_id=run.id,
                logs=[
                    FunctionLogEntry(
                        timestamp="2026-06-02T00:00:00Z",
                        stream="stdout",
                        message="done",
                    )
                ],
            ),
        ],
    )

    result = await _execute(service, function, run, user_email=run.user_email)

    assert result.status == FunctionRunStatus.COMPLETED
    assert result.output_data == {"done": True}
    # RUNNING transition on update_run; terminal COMPLETED on update_run_and_collect.
    assert run_repo.update_run.await_count == 1
    assert run_repo.update_run_and_collect.await_count == 1
    persisted_run = run_repo.update_run_and_collect.await_args.args[0]
    completed_events = [
        e
        for e in persisted_run.collect_events()
        if isinstance(e, FunctionRunCompletedEvent)
    ]
    assert len(completed_events) == 1
    assert completed_events[0].run_id == run.id
    assert session.exec_calls == []
    assert clients[0].execute_calls[-1]["request"].async_job is True
    assert clients[0].ready_calls, "wait_until_ready must run before execute"
    assert clients[1].status_calls[-1]["run_id"] == run.id
    assert clients[1].log_calls[-1]["run_id"] == run.id


async def _run_api_function(
    service: FunctionService,
    function_repo: AsyncMock,
    run_repo: AsyncMock,
    workspace_service: AsyncMock,
    ctx: Context,
    responses: list,
):
    function = _function_entity(code_path="f.py")
    function_repo.get_by_name.return_value = function
    run = _run_entity(function_id=function.id, user_id=function.user_id, input_data={"a": 1})
    run_repo.create_run.return_value = run
    workspace_service.get_session.return_value = _FakeSession([])
    clients = _install_executor(service, responses)
    result = await _execute(service, function, run)
    return result, run, clients


def _completed_response(**overrides) -> FunctionInvokeResponse:
    payload = {
        "status": "completed",
        "output_data": {"done": True},
        "logs": [],
        "code_hash": "abc",
        "duration_ms": 10,
    }
    payload.update(overrides)
    return FunctionInvokeResponse(**payload)


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://agentbox.test/function-executor")
    response = httpx.Response(status_code, request=request, text="{}")
    return httpx.HTTPStatusError("error", request=request, response=response)


async def test_execute_waits_for_readiness_before_executing(
    service: FunctionService,
    function_repo: AsyncMock,
    run_repo: AsyncMock,
    workspace_service: AsyncMock,
    ctx: Context,
):
    result, _run, clients = await _run_api_function(
        service, function_repo, run_repo, workspace_service, ctx, [_completed_response()]
    )

    assert result.status == FunctionRunStatus.COMPLETED
    assert clients[0].ready_calls, "wait_until_ready must be invoked"
    # Readiness is probed strictly before the execute is posted.
    assert clients[0].event_order[: clients[0].event_order.index("execute")] == ["ready"]


async def test_execute_retries_transient_502_then_succeeds(
    service: FunctionService,
    function_repo: AsyncMock,
    run_repo: AsyncMock,
    workspace_service: AsyncMock,
    ctx: Context,
    monkeypatch,
):
    import app.modules.function.application.function_run_executor as fs
    import app.modules.workspace.agentbox_retry as retry_mod

    monkeypatch.setattr(fs, "_FUNCTION_EXECUTE_RETRY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(retry_mod.asyncio, "sleep", AsyncMock())

    result, _run, clients = await _run_api_function(
        service,
        function_repo,
        run_repo,
        workspace_service,
        ctx,
        [_http_error(502), _completed_response()],
    )

    assert result.status == FunctionRunStatus.COMPLETED
    assert len(clients[0].execute_calls) == 2  # retried once past the 502


async def test_execute_does_not_retry_failed_response(
    service: FunctionService,
    function_repo: AsyncMock,
    run_repo: AsyncMock,
    workspace_service: AsyncMock,
    ctx: Context,
    monkeypatch,
):
    import app.modules.function.application.function_run_executor as fs

    # Even with retries available, a 200 carrying status="failed" is a real
    # function failure and must NOT be retried.
    monkeypatch.setattr(fs, "_FUNCTION_EXECUTE_RETRY_MAX_ATTEMPTS", 5)

    result, _run, clients = await _run_api_function(
        service,
        function_repo,
        run_repo,
        workspace_service,
        ctx,
        [
            FunctionInvokeResponse(
                status="failed",
                error=RuntimeErrorInfo(name="RuntimeError", message="boom"),
                logs=[],
                code_hash="abc",
                duration_ms=10,
            )
        ],
    )

    assert result.status == FunctionRunStatus.FAILED
    assert len(clients[0].execute_calls) == 1


async def test_api_completion_emits_completed_event(
    service: FunctionService,
    function_repo: AsyncMock,
    run_repo: AsyncMock,
    workspace_service: AsyncMock,
    ctx: Context,
):
    result, run, _clients = await _run_api_function(
        service,
        function_repo,
        run_repo,
        workspace_service,
        ctx,
        [_completed_response(output_data={"ok": 1})],
    )

    assert result.status == FunctionRunStatus.COMPLETED
    persisted_run = run_repo.update_run_and_collect.await_args.args[0]
    events = persisted_run.collect_events()
    assert len(events) == 1
    assert isinstance(events[0], FunctionRunCompletedEvent)
    assert events[0].run_id == run.id
    assert events[0].output_data == {"ok": 1}


async def test_api_failure_emits_failed_event(
    service: FunctionService,
    function_repo: AsyncMock,
    run_repo: AsyncMock,
    workspace_service: AsyncMock,
    ctx: Context,
):
    result, run, _clients = await _run_api_function(
        service,
        function_repo,
        run_repo,
        workspace_service,
        ctx,
        [
            FunctionInvokeResponse(
                status="failed",
                error=RuntimeErrorInfo(name="RuntimeError", message="kaboom"),
                logs=[],
                code_hash="abc",
                duration_ms=10,
            )
        ],
    )

    assert result.status == FunctionRunStatus.FAILED
    persisted_run = run_repo.update_run_and_collect.await_args.args[0]
    events = persisted_run.collect_events()
    assert len(events) == 1
    assert isinstance(events[0], FunctionRunFailedEvent)
    assert events[0].run_id == run.id
    assert events[0].error == "kaboom"


async def test_keep_sandbox_alive_heartbeats_sandbox_until_exit(
    service: FunctionService,
    monkeypatch,
):
    """While a JOB occupies the sandbox the keepalive heartbeats it, and stops
    the moment the run finishes (context exit)."""
    import app.modules.function.application.function_run_executor as fs

    monkeypatch.setattr(fs, "_SANDBOX_HEARTBEAT_INTERVAL_SECONDS", 0.01)

    calls: list[str] = []

    class _Client:
        async def heartbeat_sandbox(self, sandbox_id: str) -> bool:
            calls.append(sandbox_id)
            return True

    class _Session:
        sandbox_id = "sandbox-1"
        client = _Client()

    async with service._executor._keep_sandbox_alive(_Session()):
        await asyncio.sleep(0.05)

    during = len(calls)
    assert during >= 1
    assert all(sandbox_id == "sandbox-1" for sandbox_id in calls)

    # The heartbeat task is cancelled on exit -- no more calls afterwards.
    await asyncio.sleep(0.05)
    assert len(calls) == during


async def test_keep_sandbox_alive_noop_without_manager_client(
    service: FunctionService,
):
    """A session that exposes no manager client (e.g. a test double) must not
    break the keepalive -- it simply does nothing."""

    class _Session:
        sandbox_id = "sandbox-1"

    async with service._executor._keep_sandbox_alive(_Session()):
        await asyncio.sleep(0.01)


def _gateway_timeout_error() -> httpx.HTTPStatusError:
    request = httpx.Request(
        "POST",
        "https://agentbox.test/sandboxes/sandbox-1/apps/function_executor/"
        "pods/p/functions/f/execute",
    )
    response = httpx.Response(504, request=request, text="upstream timed out")
    return httpx.HTTPStatusError("gateway timeout", request=request, response=response)


async def test_execute_function_api_sync_504_times_out_without_retry(
    service: FunctionService,
    function_repo: AsyncMock,
    run_repo: AsyncMock,
    workspace_service: AsyncMock,
    ctx: Context,
):
    """A synchronous (API) execute that 504s at the proxy must NOT be retried --
    the function may have run -- and surfaces as a timeout, not a re-run."""
    function = _function_entity(code_path="f.py")
    function_repo.get_by_name.return_value = function
    run = _run_entity(
        function_id=function.id, user_id=function.user_id, input_data={"a": 1}
    )
    run_repo.create_run.return_value = run
    workspace_service.get_session.return_value = _FakeSession([])
    clients = _install_executor(service, [_gateway_timeout_error()])

    result = await _execute(service, function, run)

    assert result.status == FunctionRunStatus.FAILED
    assert "execution timeout" in (result.error or "").lower()
    # Non-idempotent sync execute: the 504 is terminal, called exactly once.
    assert len(clients[0].execute_calls) == 1
