from __future__ import annotations

from sandbox_runtime.errors import SandboxUnavailable

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest


from app.modules.function.application.function_dispatcher import (
    FunctionDispatcher,
    InvocationOutcomeUnconfirmed,
)
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


def _endpoint(url: str) -> FunctionRuntimeEndpoint:
    return FunctionRuntimeEndpoint(
        url=url,
        request_headers=(("E2B-Traffic-Access-Token", "provider-secret"),),
        allocation_id=uuid4(),
        allocation_epoch=1,
        profile_digest=f"sha256:{'2' * 64}",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
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
        sandbox_client_factory=AsyncMock,
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
    endpoint = _endpoint("https://sandbox.test/runtime/")
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
    assert observed["headers"]["E2B-Traffic-Access-Token"] == "provider-secret"
    assert "X-API-Key" not in observed["headers"]
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
            return_value=_endpoint("https://sandbox.test/runtime/")
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
    assert observed["headers"]["E2B-Traffic-Access-Token"] == "provider-secret"


@pytest.mark.asyncio
async def test_ambiguous_response_is_not_replayed() -> None:
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
    endpoint = _endpoint("https://sandbox.test/runtime/")

    with pytest.raises(InvocationOutcomeUnconfirmed):
        await dispatcher._invoke_runtime_with_recovery(
            dispatch,
            context=context,
            endpoint=endpoint,
            function_token="delegated-function-token",
            organization_id=None,
        )

    assert len(requests) == 1


@pytest.mark.asyncio
async def test_unavailable_allocation_is_refreshed_once_before_runtime_work(
    monkeypatch,
) -> None:
    dispatch = _dispatch()
    context = _context(dispatch)
    requests = []

    class _Runtime:
        async def post(self, url, **kwargs):
            requests.append((url, kwargs))
            if len(requests) == 1:
                return httpx.Response(
                    410,
                    request=httpx.Request("POST", url),
                )
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
    refreshed = _endpoint("https://sandbox.test/fresh-allocation/")
    resolver = AsyncMock(return_value=refreshed)
    monkeypatch.setattr(dispatcher, "_runtime_endpoint", resolver)

    result = await dispatcher._invoke_runtime_with_recovery(
        dispatch,
        context=context,
        endpoint=_endpoint("https://sandbox.test/stale-allocation/"),
        function_token="delegated-function-token",
        organization_id=None,
    )

    assert result.status == "completed"
    assert [str(request[0]) for request in requests] == [
        (
            "https://sandbox.test/stale-allocation/"
            f"functions/{dispatch.function_id}/runs/{dispatch.run_id}"
        ),
        (
            "https://sandbox.test/fresh-allocation/"
            f"functions/{dispatch.function_id}/runs/{dispatch.run_id}"
        ),
    ]
    resolver.assert_awaited_once_with(dispatch)


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
        endpoint=_endpoint("https://sandbox.test/exact-allocation/"),
    )

    assert str(observed["url"]).endswith(
        f"/functions/{dispatch.function_id}/runs/{dispatch.run_id}:cancel"
    )
    assert observed["kwargs"]["headers"] == {
        "E2B-Traffic-Access-Token": "provider-secret"
    }


def test_timeout_keeps_stable_user_facing_error() -> None:
    assert FunctionDispatcher._execution_error(TimeoutError()) == (
        "Function execution timed out (deadline exceeded)"
    )


def test_capacity_exhaustion_reports_pre_execution_failure() -> None:
    error = SandboxUnavailable(
        "provider active sandbox capacity is exhausted",
        retry_after_ms=1_000,
    )

    # The provider's own words reach the run, rather than a fixed sentence
    # keyed off an error code.
    assert FunctionDispatcher._execution_error(error) == (
        "Function sandbox unavailable "
        "(provider active sandbox capacity is exhausted)"
    )
