from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from agentbox_client import (
    AgentBoxApiError,
    PortAccessGrant,
    RetryDisposition,
    SandboxHandle,
)
from agentbox_client.models import (
    AgentBoxErrorBody,
    AgentBoxErrorResponse,
    PortProtocol,
    ProfileRef,
    WorkloadKind,
)

from app.modules.function.application.function_callback_credentials import (
    FunctionCallbackCredentialSigner,
)
from app.modules.function.application.function_dispatcher import FunctionDispatcher
from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpointCache,
)
from app.modules.function.application.function_session_token_cache import (
    FunctionSessionTokenCache,
)
from app.modules.function.domain.entities import (
    FunctionDispatchMode,
    FunctionExecutionDispatch,
    FunctionRunEntity,
    FunctionRunStatus,
)


class _UowTracker:
    active = 0

    @asynccontextmanager
    async def factory(self):
        self.active += 1
        try:
            yield object()
        finally:
            self.active -= 1


async def _token_minter(**_kwargs) -> str:
    return "delegated-function-token"


def _dispatch(
    *,
    mode: FunctionDispatchMode = FunctionDispatchMode.SYNCHRONOUS,
):
    return FunctionExecutionDispatch(
        run_id=uuid4(),
        pod_id=uuid4(),
        function_id=uuid4(),
        function_name="test-function",
        user_id=uuid4(),
        mode=mode,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        revision_hash=f"sha256:{'a' * 64}",
        input_data={"value": 7},
    )


def _run(
    dispatch: FunctionExecutionDispatch,
    status: FunctionRunStatus,
) -> FunctionRunEntity:
    return FunctionRunEntity(
        id=dispatch.run_id,
        function_id=dispatch.function_id,
        revision_hash=dispatch.revision_hash,
        user_id=dispatch.user_id,
        status=status,
        deadline_at=dispatch.deadline_at,
    )


def _sandbox_client(
    tracker: _UowTracker,
    dispatch: FunctionExecutionDispatch,
):
    class _Client:
        async def ensure_sandbox(self, kind, logical_id, **_kwargs):
            assert tracker.active == 0
            return SandboxHandle(
                workload_kind=kind,
                logical_id=logical_id,
                desired_state="present",
                profile=ProfileRef(
                    name="function-python-v1", digest=f"sha256:{'2' * 64}"
                ),
                allocation_state="active",
                allocation_id=uuid4(),
                allocation_epoch=1,
                ready=True,
                operation_id=None,
                retry_after_ms=None,
            )

        async def create_port_access(self, kind, logical_id, port, **_kwargs):
            assert tracker.active == 0
            assert (kind, logical_id, port) == (
                WorkloadKind.FUNCTION,
                dispatch.pod_id,
                8090,
            )
            return PortAccessGrant(
                workload_kind=kind,
                logical_id=logical_id,
                port=port,
                protocol=PortProtocol.HTTP,
                url="https://agentbox.test/port-access/signed/",
                expires_at=dispatch.deadline_at,
            )

        async def close(self):
            assert tracker.active == 0

    return _Client


def _dispatcher(
    tracker: _UowTracker,
    signer: FunctionCallbackCredentialSigner,
    client_factory,
) -> FunctionDispatcher:
    return FunctionDispatcher(
        uow_factory=tracker.factory,
        credential_signer=signer,
        agentbox_client_factory=client_factory,
        token_minter=_token_minter,
        token_cache=FunctionSessionTokenCache(),
        endpoint_cache=FunctionRuntimeEndpointCache(),
        runtime_http_client_factory=lambda: httpx.AsyncClient(follow_redirects=False),
        delegated_tokens_enabled=True,
    )


def test_agentbox_deadline_error_keeps_stable_timeout_message() -> None:
    response = httpx.Response(
        408,
        request=httpx.Request("POST", "https://agentbox.test/processes"),
    )
    error = AgentBoxApiError(
        response,
        AgentBoxErrorResponse(
            error=AgentBoxErrorBody(
                code="DEADLINE_EXCEEDED",
                message="process deadline has elapsed",
                retry=RetryDisposition.DO_NOT_RETRY,
            )
        ),
    )

    assert FunctionDispatcher._execution_error(error) == (
        "Function execution timed out (deadline exceeded)"
    )


@pytest.mark.asyncio
async def test_dispatcher_invokes_run_once_and_holds_no_db_during_io(
    monkeypatch,
) -> None:
    tracker = _UowTracker()
    dispatch = _dispatch()
    pending = _run(dispatch, FunctionRunStatus.PENDING)
    completed = _run(dispatch, FunctionRunStatus.COMPLETED).model_copy(
        update={"output_data": {"ok": True}}
    )
    terminal_persisted = False

    class _ExecutionRepository:
        def __init__(self, _uow, _signer):
            pass

        async def resolve_dispatch(self, *_args, **_kwargs):
            return dispatch

        async def fail_dispatch(self, *_args, **_kwargs):
            raise AssertionError("successful execution must not fail")

    class _RunRepository:
        def __init__(self, _uow):
            pass

        async def get_run(self, _run_id):
            return completed if terminal_persisted else pending

    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher."
        "FunctionExecutionRepository",
        _ExecutionRepository,
    )
    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher.FunctionRunRepository",
        _RunRepository,
    )
    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher.settings."
        "function_runtime_gateway_url",
        "http://127.0.0.1:8711",
    )
    requests: list[tuple[str, dict]] = []

    class _RuntimeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json=None, timeout=None):
            nonlocal terminal_persisted
            assert tracker.active == 0
            requests.append((url, {"headers": headers, "json": json}))
            terminal_persisted = True
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "status": "completed",
                    "output_data": {"ok": True},
                    "error": None,
                    "stdout": "",
                    "stderr": "",
                    "output_truncated": False,
                },
            )

    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher.httpx.AsyncClient",
        lambda **_kwargs: _RuntimeClient(),
    )
    signer = FunctionCallbackCredentialSigner("d" * 32)
    result = await _dispatcher(
        tracker,
        signer,
        _sandbox_client(tracker, dispatch),
    ).execute(dispatch.run_id, mode=FunctionDispatchMode.SYNCHRONOUS)

    assert result.status == FunctionRunStatus.COMPLETED
    assert len(requests) == 1
    url, request = requests[0]
    assert url.endswith(f"/functions/{dispatch.function_id}/runs/{dispatch.run_id}")
    assert request["json"] == {"input": dispatch.input_data}
    assert request["headers"]["Authorization"] == "Bearer delegated-function-token"
    assert request["headers"]["If-Match"] == f'"{dispatch.revision_hash}"'
    assert request["headers"]["X-Lemma-Gateway-Url"] == "http://127.0.0.1:8711"
    assert request["headers"]["X-Lemma-Run-Token"] == signer.derive(dispatch.run_id)
    assert "Idempotency-Key" not in request["headers"]
    assert tracker.active == 0


@pytest.mark.asyncio
async def test_job_returns_after_runtime_claim_without_polling_terminal(
    monkeypatch,
) -> None:
    tracker = _UowTracker()
    dispatch = _dispatch(mode=FunctionDispatchMode.ASYNCHRONOUS)
    running = _run(dispatch, FunctionRunStatus.RUNNING)
    loads = 0

    class _ExecutionRepository:
        def __init__(self, _uow, _signer):
            pass

        async def resolve_dispatch(self, *_args, **_kwargs):
            return dispatch

        async def fail_dispatch(self, *_args, **_kwargs):
            raise AssertionError("accepted JOB execution must not fail")

    class _RunRepository:
        def __init__(self, _uow):
            pass

        async def get_run(self, _run_id):
            nonlocal loads
            loads += 1
            return running

    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher."
        "FunctionExecutionRepository",
        _ExecutionRepository,
    )
    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher.FunctionRunRepository",
        _RunRepository,
    )
    requests: list[dict] = []

    class _RuntimeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json=None, timeout=None):
            assert tracker.active == 0
            requests.append({"url": url, "headers": headers, "json": json})
            return httpx.Response(
                202,
                request=httpx.Request("POST", url),
                headers={"Preference-Applied": "respond-async"},
                json={"accepted": True, "run_id": str(dispatch.run_id)},
            )

    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher.httpx.AsyncClient",
        lambda **_kwargs: _RuntimeClient(),
    )
    signer = FunctionCallbackCredentialSigner("j" * 32)
    result = await _dispatcher(
        tracker,
        signer,
        _sandbox_client(tracker, dispatch),
    ).execute(dispatch.run_id, mode=FunctionDispatchMode.ASYNCHRONOUS)

    assert result.status == FunctionRunStatus.RUNNING
    assert loads == 1
    assert len(requests) == 1
    assert requests[0]["headers"]["Prefer"] == "respond-async"
    assert requests[0]["headers"]["X-Lemma-Run-Token"] == signer.derive(dispatch.run_id)
    assert requests[0]["json"] == {"input": dispatch.input_data}
    assert tracker.active == 0


@pytest.mark.asyncio
async def test_lost_response_reconciles_terminal_callback_without_failing(
    monkeypatch,
) -> None:
    tracker = _UowTracker()
    dispatch = _dispatch()
    completed = _run(dispatch, FunctionRunStatus.COMPLETED)

    class _ExecutionRepository:
        def __init__(self, _uow, _signer):
            pass

        async def resolve_dispatch(self, *_args, **_kwargs):
            return dispatch

        async def fail_dispatch(self, *_args, **_kwargs):
            raise AssertionError("persisted terminal callback must win")

    class _RunRepository:
        def __init__(self, _uow):
            pass

        async def get_run(self, _run_id):
            return completed

    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher."
        "FunctionExecutionRepository",
        _ExecutionRepository,
    )
    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher.FunctionRunRepository",
        _RunRepository,
    )

    class _RuntimeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **_kwargs):
            raise httpx.ReadError("response lost", request=httpx.Request("POST", url))

    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher.httpx.AsyncClient",
        lambda **_kwargs: _RuntimeClient(),
    )
    result = await _dispatcher(
        tracker,
        FunctionCallbackCredentialSigner("e" * 32),
        _sandbox_client(tracker, dispatch),
    ).execute(dispatch.run_id, mode=FunctionDispatchMode.SYNCHRONOUS)

    assert result.status == FunctionRunStatus.COMPLETED


@pytest.mark.asyncio
async def test_lost_job_ack_reconciles_durable_runtime_claim_without_failing(
    monkeypatch,
) -> None:
    tracker = _UowTracker()
    dispatch = _dispatch(mode=FunctionDispatchMode.ASYNCHRONOUS)
    running = _run(dispatch, FunctionRunStatus.RUNNING)

    class _ExecutionRepository:
        def __init__(self, _uow, _signer):
            pass

        async def resolve_dispatch(self, *_args, **_kwargs):
            return dispatch

        async def fail_dispatch(self, *_args, **_kwargs):
            raise AssertionError("durably claimed JOB execution must not fail")

    class _RunRepository:
        def __init__(self, _uow):
            pass

        async def get_run(self, _run_id):
            return running

    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher."
        "FunctionExecutionRepository",
        _ExecutionRepository,
    )
    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher.FunctionRunRepository",
        _RunRepository,
    )

    class _RuntimeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **_kwargs):
            raise httpx.ReadError("response lost", request=httpx.Request("POST", url))

    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher.httpx.AsyncClient",
        lambda **_kwargs: _RuntimeClient(),
    )
    result = await _dispatcher(
        tracker,
        FunctionCallbackCredentialSigner("k" * 32),
        _sandbox_client(tracker, dispatch),
    ).execute(dispatch.run_id, mode=FunctionDispatchMode.ASYNCHRONOUS)

    assert result.status == FunctionRunStatus.RUNNING


@pytest.mark.asyncio
async def test_lost_job_request_retries_same_operation_once(
    monkeypatch,
) -> None:
    tracker = _UowTracker()
    dispatch = _dispatch(mode=FunctionDispatchMode.ASYNCHRONOUS)
    pending = _run(dispatch, FunctionRunStatus.PENDING)
    running = _run(dispatch, FunctionRunStatus.RUNNING)
    loads = iter((pending, running))

    class _ExecutionRepository:
        def __init__(self, _uow, _signer):
            pass

        async def resolve_dispatch(self, *_args, **_kwargs):
            return dispatch

        async def fail_dispatch(self, *_args, **_kwargs):
            raise AssertionError("acknowledged retry must not fail")

    class _RunRepository:
        def __init__(self, _uow):
            pass

        async def get_run(self, _run_id):
            return next(loads)

    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher."
        "FunctionExecutionRepository",
        _ExecutionRepository,
    )
    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher.FunctionRunRepository",
        _RunRepository,
    )
    calls = 0

    class _RuntimeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ReadError(
                    "request lost",
                    request=httpx.Request("POST", url),
                )
            return httpx.Response(
                202,
                request=httpx.Request("POST", url),
                headers={"Preference-Applied": "respond-async"},
                json={"accepted": True, "run_id": str(dispatch.run_id)},
            )

    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher.httpx.AsyncClient",
        lambda **_kwargs: _RuntimeClient(),
    )
    result = await _dispatcher(
        tracker,
        FunctionCallbackCredentialSigner("l" * 32),
        _sandbox_client(tracker, dispatch),
    ).execute(dispatch.run_id, mode=FunctionDispatchMode.ASYNCHRONOUS)

    assert result.status == FunctionRunStatus.RUNNING
    assert calls == 2


@pytest.mark.asyncio
async def test_repeated_lost_response_fails_and_cancels_exact_run(
    monkeypatch,
) -> None:
    tracker = _UowTracker()
    dispatch = _dispatch(mode=FunctionDispatchMode.ASYNCHRONOUS)
    pending = _run(dispatch, FunctionRunStatus.PENDING)
    failed = _run(dispatch, FunctionRunStatus.FAILED)
    fail_errors: list[str] = []
    calls = {"invoke": 0, "cancel": 0}
    signer = FunctionCallbackCredentialSigner("f" * 32)

    class _ExecutionRepository:
        def __init__(self, _uow, _signer):
            pass

        async def resolve_dispatch(self, *_args, **_kwargs):
            return dispatch

        async def fail_dispatch(self, _dispatch, *, error):
            fail_errors.append(error)
            return failed

    class _RunRepository:
        def __init__(self, _uow):
            pass

        async def get_run(self, _run_id):
            return pending

    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher."
        "FunctionExecutionRepository",
        _ExecutionRepository,
    )
    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher.FunctionRunRepository",
        _RunRepository,
    )

    class _RuntimeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, **_kwargs):
            assert tracker.active == 0
            if url.endswith(":cancel"):
                calls["cancel"] += 1
                assert url.endswith(f"/runs/{dispatch.run_id}:cancel")
                assert (
                    headers["Authorization"]
                    == f"Bearer {signer.derive(dispatch.run_id)}"
                )
                return httpx.Response(
                    202,
                    request=httpx.Request("POST", url),
                    json={"accepted": True},
                )
            calls["invoke"] += 1
            raise httpx.ReadError("response lost", request=httpx.Request("POST", url))

    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher.httpx.AsyncClient",
        lambda **_kwargs: _RuntimeClient(),
    )
    result = await _dispatcher(
        tracker,
        signer,
        _sandbox_client(tracker, dispatch),
    ).execute(dispatch.run_id, mode=FunctionDispatchMode.ASYNCHRONOUS)

    assert result.status == FunctionRunStatus.FAILED
    assert calls == {"invoke": 2, "cancel": 1}
    assert fail_errors == [
        "Function execution failed because the runtime response was not confirmed"
    ]


@pytest.mark.asyncio
async def test_cancel_targets_exact_run_then_persists_cancelled(monkeypatch) -> None:
    tracker = _UowTracker()
    dispatch = _dispatch()
    cancelled = _run(dispatch, FunctionRunStatus.CANCELLED)
    signer = FunctionCallbackCredentialSigner("g" * 32)
    persisted = False

    class _ExecutionRepository:
        def __init__(self, _uow, _signer):
            pass

        async def active_dispatch(self, _run_id, **_kwargs):
            return dispatch

        async def cancel_dispatch(self, received):
            nonlocal persisted
            assert received == dispatch
            persisted = True
            return cancelled

    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher."
        "FunctionExecutionRepository",
        _ExecutionRepository,
    )
    runtime_calls: list[tuple[str, str]] = []

    class _RuntimeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, **_kwargs):
            assert tracker.active == 0
            assert not persisted
            runtime_calls.append((url, headers["Authorization"]))
            return httpx.Response(
                202,
                request=httpx.Request("POST", url),
                json={"accepted": True},
            )

    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher.httpx.AsyncClient",
        lambda **_kwargs: _RuntimeClient(),
    )
    result = await _dispatcher(
        tracker,
        signer,
        _sandbox_client(tracker, dispatch),
    ).cancel(dispatch.run_id)

    assert result.status == FunctionRunStatus.CANCELLED
    assert runtime_calls == [
        (
            f"https://agentbox.test/port-access/signed/runs/{dispatch.run_id}:cancel",
            f"Bearer {signer.derive(dispatch.run_id)}",
        )
    ]
    assert persisted
