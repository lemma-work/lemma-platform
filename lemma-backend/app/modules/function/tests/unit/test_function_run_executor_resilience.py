"""Unit tests for the function-run executor's resilience behaviour:

* Fix 1 (outer): a synchronous (non-idempotent) execute does not re-run the
  whole function on an ambiguous post-dispatch error, but still recovers from a
  "request provably never ran" error.
* Fix 3: backend<->sandbox failures surface a clean, user-facing ``run.error``
  with no server tracebacks / raw HTTP bodies / ``Errno`` strings.
"""

from __future__ import annotations

import asyncio
import httpx
import pytest
from agentbox_client.apps.function_executor import (
    FunctionInvokeResponse,
    RuntimeErrorInfo,
)
from types import SimpleNamespace
from uuid import uuid4

from app.core.log import log as _log_module  # noqa: F401  (ensure logging import OK)
from app.modules.function.application import function_run_executor as fre
from app.modules.function.application.function_run_executor import (
    _NON_IDEMPOTENT_RECOVERABLE_SANDBOX_STATUS_CODES,
    _NON_IDEMPOTENT_RECOVERABLE_SANDBOX_TRANSPORT_ERRORS,
    FunctionRunExecutor,
)
from app.modules.function.domain.errors import FunctionValidationError
from app.modules.function.domain.entities import FunctionEntity, FunctionRunEntity
from app.modules.workspace.agentbox_retry import REQUEST_NOT_DELIVERED_HEADER

pytestmark = pytest.mark.asyncio


def _executor() -> FunctionRunExecutor:
    return FunctionRunExecutor(workspace_service=None, storage_factory=None)


def _http_status_error(
    status_code: int,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://sandbox.test/execute")
    response = httpx.Response(
        status_code,
        request=request,
        text="internal stack trace leak",
        headers=headers,
    )
    return httpx.HTTPStatusError("error", request=request, response=response)


class _FakeRun:
    id = "run-1"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _instant(_delay):
        return None

    monkeypatch.setattr(fre.asyncio, "sleep", _instant)


# --------------------------------------------------------------------------
# Fix 3 — clean, user-facing error mapping (no server internals)
# --------------------------------------------------------------------------

_LEAK_SUBSTRINGS = (
    "Traceback",
    "Errno",
    "104",
    "Connection reset",
    "stack trace",
    "sandbox.test",
)


@pytest.mark.parametrize(
    "exc,expected_fragment",
    [
        (ConnectionResetError(104, "Connection reset by peer"), "interrupted"),
        (httpx.ReadError("boom"), "interrupted"),
        (httpx.RemoteProtocolError("server disconnected"), "interrupted"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (TimeoutError("deadline"), "timeout"),
        (_http_status_error(503), "temporarily unavailable"),
        (_http_status_error(500), "unexpected error"),
        (ValueError("kaboom internal"), "internal error"),
    ],
)
async def test_user_facing_error_is_clean(exc, expected_fragment):
    msg = _executor()._user_facing_execution_error(exc)
    assert expected_fragment in msg.lower()
    for leak in _LEAK_SUBSTRINGS:
        assert leak not in msg


async def test_function_validation_error_message_passes_through():
    msg = _executor()._user_facing_execution_error(
        FunctionValidationError("Input does not match the declared schema.")
    )
    assert msg == "Input does not match the declared schema."


async def test_executor_response_redacts_persisted_error_and_logs():
    run = FunctionRunEntity(function_id=uuid4(), user_id=uuid4())
    response = FunctionInvokeResponse(
        status="failed",
        error=RuntimeErrorInfo(
            name="ProviderError",
            message="api_key=CANARY_FUNCTION_SECRET",
        ),
        logs=[
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "stream": "stderr",
                "message": "Authorization: Bearer CANARY_FUNCTION_SECRET",
            }
        ],
        code_hash="hash",
        duration_ms=1,
    )

    _executor()._apply_executor_response_to_run(run, response)

    assert "CANARY_FUNCTION_SECRET" not in str(run.model_dump())
    assert "[REDACTED]" in str(run.model_dump())


# --------------------------------------------------------------------------
# Fix 1 (outer) — sandbox-recovery is idempotency-aware for sync executes
# --------------------------------------------------------------------------


async def test_is_recoverable_matrix_default_vs_narrowed():
    # Default (async-job / JOB path): the full set still recovers read errors + 5xx.
    assert (
        FunctionRunExecutor._is_recoverable_sandbox_error(httpx.ReadError("x")) is True
    )
    assert (
        FunctionRunExecutor._is_recoverable_sandbox_error(_http_status_error(500))
        is True
    )

    # Narrowed (sync / non-idempotent): only "request provably never ran" errors.
    narrow = dict(
        status_codes=_NON_IDEMPOTENT_RECOVERABLE_SANDBOX_STATUS_CODES,
        transport_errors=_NON_IDEMPOTENT_RECOVERABLE_SANDBOX_TRANSPORT_ERRORS,
    )
    assert (
        FunctionRunExecutor._is_recoverable_sandbox_error(
            httpx.ReadError("x"), **narrow
        )
        is False
    )
    assert (
        FunctionRunExecutor._is_recoverable_sandbox_error(
            _http_status_error(500), **narrow
        )
        is False
    )
    assert (
        FunctionRunExecutor._is_recoverable_sandbox_error(
            httpx.ConnectError("x"), **narrow
        )
        is True
    )
    assert (
        FunctionRunExecutor._is_recoverable_sandbox_error(
            _http_status_error(404), **narrow
        )
        is True
    )


async def test_sync_recovery_does_not_rerun_on_post_dispatch_error():
    calls = {"n": 0}

    async def _attempt():
        calls["n"] += 1
        raise httpx.ReadError("response-leg failure after the function ran")

    with pytest.raises(httpx.ReadError):
        await _executor()._execute_with_sandbox_recovery(
            run=_FakeRun(),
            make_attempt=_attempt,
            recoverable_status_codes=_NON_IDEMPOTENT_RECOVERABLE_SANDBOX_STATUS_CODES,
            recoverable_transport_errors=_NON_IDEMPOTENT_RECOVERABLE_SANDBOX_TRANSPORT_ERRORS,
        )
    # No re-run — the function may already have executed its side effect.
    assert calls["n"] == 1


async def test_sync_execute_retries_same_run_id_on_transient_http_503():
    calls = {"n": 0}

    class _Client:
        async def wait_until_ready(self, **_kwargs):
            return None

        async def execute(self, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _http_status_error(503)
            return FunctionInvokeResponse(
                status="completed",
                output_data={"ok": True},
                code_hash="hash",
                duration_ms=1,
            )

        async def close(self):
            return None

    executor = FunctionRunExecutor(
        workspace_service=None,
        storage_factory=None,
        function_executor_client_factory=lambda _token: _Client(),
    )
    function_id = uuid4()
    function = FunctionEntity(
        id=function_id,
        pod_id=uuid4(),
        user_id=uuid4(),
        name="side_effect",
    )
    run = FunctionRunEntity(
        id=uuid4(),
        function_id=function_id,
        user_id=function.user_id,
    )
    session = SimpleNamespace(
        env_vars={"LEMMA_TOKEN": "test-token"},
        sandbox_id="sandbox",
    )

    response = await executor._execute_via_function_executor(
        function=function,
        run=run,
        session=session,
        timeout_seconds=30,
        async_job=False,
    )

    assert response.status == "completed"
    assert calls["n"] == 2


async def test_sync_execute_retries_manager_proven_pre_routing_503():
    calls = {"n": 0}

    class _Client:
        async def wait_until_ready(self, **_kwargs):
            return None

        async def execute(self, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _http_status_error(
                    503,
                    headers={REQUEST_NOT_DELIVERED_HEADER: "true"},
                )
            return FunctionInvokeResponse(
                status="completed",
                output_data={"ok": True},
                code_hash="hash",
                duration_ms=1,
            )

        async def close(self):
            return None

    executor = FunctionRunExecutor(
        workspace_service=None,
        storage_factory=None,
        function_executor_client_factory=lambda _token: _Client(),
    )
    function_id = uuid4()
    function = FunctionEntity(
        id=function_id,
        pod_id=uuid4(),
        user_id=uuid4(),
        name="side_effect",
    )
    run = FunctionRunEntity(
        id=uuid4(),
        function_id=function_id,
        user_id=function.user_id,
    )
    session = SimpleNamespace(
        env_vars={"LEMMA_TOKEN": "test-token"},
        sandbox_id="sandbox",
    )

    response = await executor._execute_via_function_executor(
        function=function,
        run=run,
        session=session,
        timeout_seconds=30,
        async_job=False,
    )

    assert response.status == "completed"
    assert calls["n"] == 2


async def test_sync_recovery_still_recovers_when_request_never_ran():
    calls = {"n": 0}

    async def _attempt():
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("pod missing / connection refused")
        return "ok"

    result = await _executor()._execute_with_sandbox_recovery(
        run=_FakeRun(),
        make_attempt=_attempt,
        recoverable_status_codes=_NON_IDEMPOTENT_RECOVERABLE_SANDBOX_STATUS_CODES,
        recoverable_transport_errors=_NON_IDEMPOTENT_RECOVERABLE_SANDBOX_TRANSPORT_ERRORS,
    )
    assert result == "ok"
    assert calls["n"] == 2


async def test_default_recovery_reruns_read_error_for_job_path():
    # The JOB/async path keeps the full recoverable set (re-running an accepted
    # job is safe because the sandbox dedupes by run_id).
    calls = {"n": 0}

    async def _attempt():
        calls["n"] += 1
        raise httpx.ReadError("blip")

    with pytest.raises(httpx.ReadError):
        await _executor()._execute_with_sandbox_recovery(
            run=_FakeRun(), make_attempt=_attempt
        )
    assert calls["n"] == fre._SANDBOX_RECOVERY_MAX_ATTEMPTS


async def test_poll_timeout_cancels_remote_executor_run():
    calls: list[tuple[str, object]] = []

    class _Client:
        async def get_status(self, **_kwargs):
            return SimpleNamespace(status="running")

        async def cancel(self, *, sandbox_id, run_id):
            calls.append((sandbox_id, run_id))
            return True

        async def close(self):
            return None

    run_id = uuid4()
    executor = FunctionRunExecutor(
        workspace_service=None,
        storage_factory=None,
        function_executor_client_factory=lambda _token: _Client(),
    )
    with pytest.raises(TimeoutError):
        await executor._poll_executor_job(
            session=SimpleNamespace(
                env_vars={"LEMMA_TOKEN": "test-token"}, sandbox_id="sandbox-1"
            ),
            run_id=run_id,
            timeout_seconds=0,
        )
    assert calls == [("sandbox-1", run_id)]


async def test_api_and_job_poll_intervals_are_independent(monkeypatch):
    observed: list[float] = []

    async def _poll(**kwargs):
        observed.append(kwargs["poll_interval_seconds"])
        return FunctionInvokeResponse(
            status="completed",
            output_data={"ok": True},
            code_hash="hash",
            duration_ms=1,
        )

    monkeypatch.setattr(fre, "poll_session_executor_job", _poll)
    executor = _executor()
    session = SimpleNamespace(
        env_vars={"LEMMA_TOKEN": "test-token"}, sandbox_id="sandbox-1"
    )

    await executor._poll_executor_job(
        session=session,
        run_id=uuid4(),
        timeout_seconds=30,
        poll_interval_seconds=fre._API_FUNCTION_POLL_INTERVAL_SECONDS,
    )
    await executor._poll_executor_job(
        session=session,
        run_id=uuid4(),
        timeout_seconds=30,
    )

    assert observed == [
        fre._API_FUNCTION_POLL_INTERVAL_SECONDS,
        fre._JOB_FUNCTION_POLL_INTERVAL_SECONDS,
    ]


async def test_cancelled_execute_cancels_remote_executor_run():
    started = asyncio.Event()
    cancelled: list[tuple[str, object]] = []

    class _Client:
        async def wait_until_ready(self, **_kwargs):
            return None

        async def execute(self, **_kwargs):
            started.set()
            await asyncio.Event().wait()

        async def cancel(self, *, sandbox_id, run_id):
            cancelled.append((sandbox_id, run_id))
            return True

        async def close(self):
            return None

    function_id = uuid4()
    function = FunctionEntity(
        id=function_id,
        pod_id=uuid4(),
        user_id=uuid4(),
        name="long_running",
    )
    run = FunctionRunEntity(
        id=uuid4(), function_id=function_id, user_id=function.user_id
    )
    executor = FunctionRunExecutor(
        workspace_service=None,
        storage_factory=None,
        function_executor_client_factory=lambda _token: _Client(),
    )
    task = asyncio.create_task(
        executor._execute_via_function_executor(
            function=function,
            run=run,
            session=SimpleNamespace(
                env_vars={"LEMMA_TOKEN": "test-token"}, sandbox_id="sandbox-1"
            ),
            timeout_seconds=120,
            async_job=False,
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled == [("sandbox-1", run.id)]
