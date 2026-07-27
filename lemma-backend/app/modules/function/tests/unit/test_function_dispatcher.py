from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from agentbox_client import AgentBoxApiError
from agentbox_client.models import (
    AgentBoxErrorBody,
    AgentBoxErrorResponse,
    RetryDisposition,
)

from app.modules.function.application.function_dispatcher import FunctionDispatcher
from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpoint,
    FunctionRuntimeEndpointCache,
)
from app.modules.function.application.function_session_token_cache import (
    FunctionSessionToken,
    FunctionSessionTokenCache,
)
from app.modules.function.domain.entities import (
    FunctionDispatchMode,
    FunctionExecutionDispatch,
    FunctionRunEntity,
    FunctionRunRuntimeContext,
    FunctionRunStatus,
)


class _UowFactory:
    @asynccontextmanager
    async def __call__(self):
        yield object()


async def _token_minter(**_kwargs) -> FunctionSessionToken:
    return FunctionSessionToken(
        value="delegated-function-token",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


async def _organization_resolver(_pod_id):
    return str(uuid4())


def _dispatch(
    *,
    mode: FunctionDispatchMode = FunctionDispatchMode.SYNCHRONOUS,
) -> FunctionExecutionDispatch:
    return FunctionExecutionDispatch(
        run_id=uuid4(),
        pod_id=uuid4(),
        function_id=uuid4(),
        function_name="test-function",
        user_id=uuid4(),
        user_email="person@example.com",
        config={"mode": "test"},
        mode=mode,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        revision_hash=f"sha256:{'a' * 64}",
        input_data={"value": 7},
    )


def _context(dispatch: FunctionExecutionDispatch) -> FunctionRunRuntimeContext:
    return FunctionRunRuntimeContext(
        run_id=dispatch.run_id,
        deadline_at=dispatch.deadline_at,
        revision_hash=dispatch.revision_hash,
        artifact_path=f"artifacts/{'a' * 64}.zip",
        input_data=dispatch.input_data,
        config=dispatch.config,
        user_id=dispatch.user_id,
        user_email=dispatch.user_email,
        pod_id=dispatch.pod_id,
        function_id=dispatch.function_id,
        function_name=dispatch.function_name,
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
        input_data=dispatch.input_data,
        status=status,
        deadline_at=dispatch.deadline_at,
    )


def _dispatcher(runtime_client) -> FunctionDispatcher:
    return FunctionDispatcher(
        uow_factory=_UowFactory(),
        agentbox_client_factory=lambda: AsyncMock(),
        token_minter=_token_minter,
        token_cache=FunctionSessionTokenCache(),
        endpoint_cache=FunctionRuntimeEndpointCache(),
        runtime_http_client_factory=lambda: runtime_client,
        organization_resolver=_organization_resolver,
        delegated_tokens_enabled=True,
    )


@pytest.mark.asyncio
async def test_api_dispatch_sends_complete_v2_envelope_and_uses_direct_result(
    monkeypatch,
) -> None:
    dispatch = _dispatch()
    context = _context(dispatch)
    completed = _run(dispatch, FunctionRunStatus.COMPLETED)
    observed = {}

    class _Runtime:
        async def post(self, url, *, headers, json, timeout):
            observed.update(url=url, headers=headers, json=json, timeout=timeout)
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "status": "completed",
                    "output_data": {"answer": 42},
                    "error": None,
                    "stdout": "",
                    "stderr": "",
                    "output_truncated": False,
                },
            )

    dispatcher = _dispatcher(_Runtime())
    endpoint = FunctionRuntimeEndpoint(
        url="https://agentbox.test/grant/",
        expires_at=dispatch.deadline_at,
    )
    monkeypatch.setattr(
        dispatcher,
        "_resolve_dispatch",
        AsyncMock(return_value=dispatch),
    )
    monkeypatch.setattr(
        dispatcher,
        "_runtime_endpoint",
        AsyncMock(return_value=endpoint),
    )
    start = AsyncMock(return_value=context)
    complete = AsyncMock(return_value=completed)
    monkeypatch.setattr(dispatcher, "_start_dispatch", start)
    monkeypatch.setattr(dispatcher, "_complete_dispatch", complete)
    load = AsyncMock(side_effect=AssertionError("API path must not reread the run"))
    monkeypatch.setattr(dispatcher, "_load_run", load)

    result = await dispatcher.execute(
        dispatch.run_id,
        mode=FunctionDispatchMode.SYNCHRONOUS,
    )

    assert result == completed
    start.assert_awaited_once_with(dispatch)
    complete.assert_awaited_once()
    assert observed["headers"]["Authorization"] == "Bearer delegated-function-token"
    assert "X-Lemma-Run-Token" not in observed["headers"]
    assert "Prefer" not in observed["headers"]
    assert observed["json"]["protocol_version"] == 2
    assert observed["json"]["input"] == dispatch.input_data
    assert observed["json"]["config"] == dispatch.config
    assert observed["json"]["identity"]["function_id"] == str(dispatch.function_id)


@pytest.mark.asyncio
async def test_job_returns_after_runtime_acceptance_and_uses_same_function_token(
    monkeypatch,
) -> None:
    dispatch = _dispatch(mode=FunctionDispatchMode.ASYNCHRONOUS)
    context = _context(dispatch)
    running = _run(dispatch, FunctionRunStatus.RUNNING)
    observed = {}

    class _Runtime:
        async def post(self, url, *, headers, json, timeout):
            observed.update(url=url, headers=headers, json=json, timeout=timeout)
            return httpx.Response(
                202,
                request=httpx.Request("POST", url),
                json={"accepted": True, "run_id": str(dispatch.run_id)},
            )

    dispatcher = _dispatcher(_Runtime())
    monkeypatch.setattr(
        dispatcher,
        "_resolve_dispatch",
        AsyncMock(return_value=dispatch),
    )
    monkeypatch.setattr(
        dispatcher,
        "_runtime_endpoint",
        AsyncMock(
            return_value=FunctionRuntimeEndpoint(
                url="https://agentbox.test/grant/",
                expires_at=dispatch.deadline_at,
            )
        ),
    )
    monkeypatch.setattr(
        dispatcher,
        "_start_dispatch",
        AsyncMock(return_value=context),
    )
    monkeypatch.setattr(dispatcher, "_load_run", AsyncMock(return_value=running))

    result = await dispatcher.execute(
        dispatch.run_id,
        mode=FunctionDispatchMode.ASYNCHRONOUS,
    )

    assert result.status == FunctionRunStatus.RUNNING
    assert observed["headers"]["Prefer"] == "respond-async"
    assert observed["headers"]["Authorization"] == "Bearer delegated-function-token"


@pytest.mark.asyncio
async def test_ambiguous_response_retries_exact_same_allocation_once(
    monkeypatch,
) -> None:
    dispatch = _dispatch()
    context = _context(dispatch)
    requests = []

    class _Runtime:
        async def post(self, url, **kwargs):
            requests.append((url, kwargs))
            if len(requests) == 1:
                raise httpx.ReadError("lost response")
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

    dispatcher = _dispatcher(_Runtime())
    monkeypatch.setattr(dispatcher, "_wait_retry", AsyncMock())
    endpoint = FunctionRuntimeEndpoint(
        url="https://agentbox.test/exact-allocation/",
        expires_at=dispatch.deadline_at,
    )

    report = await dispatcher._invoke_runtime_with_recovery(
        dispatch,
        context=context,
        endpoint=endpoint,
        function_token="delegated-function-token",
        organization_id=None,
    )

    assert report.output_data == {"ok": True}
    assert len(requests) == 2
    assert requests[0][0] == requests[1][0]
    assert requests[0][1]["json"] == requests[1][1]["json"]


@pytest.mark.asyncio
async def test_runtime_cancel_uses_allocation_channel_without_bearer() -> None:
    dispatch = _dispatch(mode=FunctionDispatchMode.ASYNCHRONOUS)
    observed = {}

    class _Runtime:
        async def post(self, url, **kwargs):
            observed.update(url=url, kwargs=kwargs)
            return httpx.Response(202, request=httpx.Request("POST", url))

    dispatcher = _dispatcher(_Runtime())
    await dispatcher._best_effort_cancel(
        dispatch,
        endpoint=FunctionRuntimeEndpoint(
            url="https://agentbox.test/exact-allocation/",
            expires_at=dispatch.deadline_at,
        ),
    )

    assert str(observed["url"]).endswith(
        f"/functions/{dispatch.function_id}/runs/{dispatch.run_id}:cancel"
    )
    assert "headers" not in observed["kwargs"]


def test_timeout_keeps_stable_user_facing_error() -> None:
    assert FunctionDispatcher._execution_error(TimeoutError()) == (
        "Function execution timed out (deadline exceeded)"
    )


def test_capacity_exhaustion_reports_pre_execution_failure() -> None:
    error = AgentBoxApiError(
        httpx.Response(429),
        AgentBoxErrorResponse(
            error=AgentBoxErrorBody(
                code="CAPACITY_EXHAUSTED",
                message="provider active sandbox capacity is exhausted",
                retry=RetryDisposition.WAIT,
                retry_after_ms=1_000,
            )
        ),
    )

    assert FunctionDispatcher._execution_error(error) == (
        "Function sandbox capacity exhausted before execution"
    )
