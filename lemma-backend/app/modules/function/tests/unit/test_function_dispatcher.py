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


def test_terminal_logs_redacts_a_secret_that_straddles_the_size_limit():
    """Trimming before redacting must not let a credential survive the cut.

    The whole point of trimming first is to stop thirteen regex passes running
    over megabytes nobody keeps. That is only safe because the slice carries a
    margin past the limit, so a secret sitting on the boundary is still inside
    the window the patterns ran over. Without the margin its tail would be
    trimmed away and its head kept, in the clear.
    """
    from types import SimpleNamespace

    from app.core.redaction import REDACTED
    # Both the dispatcher and the runtime gateway delegate here now; they
    # each had a copy and only one of them got this fix.
    from app.modules.function.application.runtime_logs import (
        LOG_LIMIT_BYTES as _LOG_LIMIT_BYTES,
        terminal_logs,
    )

    secret = "Authorization: Bearer sk-livetokenvalue1234567890abcdefghijklmnop"
    # Land the secret so it begins just before the limit and ends after it.
    filler = "x" * (_LOG_LIMIT_BYTES - 20)
    request = SimpleNamespace(
        stdout=filler + secret + ("y" * 1024),
        stderr=None,
        output_truncated=False,
    )

    logs = terminal_logs(request)

    assert logs is not None
    # The property that matters: no part of the credential survives. The
    # [REDACTED] marker itself may fall past the final cut, because redaction
    # shortens the text and shifts everything after it left — that is fine, and
    # asserting on the marker would be asserting on arithmetic rather than on
    # the secret.
    assert "sk-livetokenvalue" not in logs
    assert len(logs) <= _LOG_LIMIT_BYTES

    # And a secret comfortably inside the limit is replaced, marker and all.
    inside = SimpleNamespace(
        stdout=f"start\n{secret}\nend", stderr=None, output_truncated=False
    )
    redacted = FunctionDispatcher._terminal_logs(inside)
    assert redacted is not None
    assert "sk-livetokenvalue" not in redacted
    assert REDACTED in redacted


# -- a runtime that has stopped serving must not be handed to the next run ----
#
# One slow cold start left an E2B sandbox whose runtime process had died: the
# VM was still running, so adoption kept succeeding, and port 8090 answered 502.
# Every later run was handed that same sandbox and failed, for 100 minutes,
# until someone deleted it by hand. 13+ consecutive failures, and nothing
# self-healed, because `InvocationOutcomeUnconfirmed` is deliberately never
# retried -- so the only thing that could have recovered it was refusing to
# hand the endpoint out again.


async def _invoke_and_capture_quarantine(runtime, *, mode=FunctionDispatchMode.SYNCHRONOUS):
    dispatch = _dispatch(mode=mode)
    dispatcher = _dispatcher(runtime)
    quarantined = AsyncMock()
    invalidated = AsyncMock()
    dispatcher._routes.quarantine = quarantined  # type: ignore[method-assign]
    dispatcher._routes.invalidate = invalidated  # type: ignore[method-assign]
    endpoint = _endpoint("https://sandbox.test/allocation/")
    with pytest.raises(InvocationOutcomeUnconfirmed):
        await dispatcher._invoke_runtime(
            dispatch,
            context=_context(dispatch),
            endpoint=endpoint,
            function_token="test-token",
            organization_id=None,
        )
    return dispatch, endpoint, quarantined, invalidated


@pytest.mark.asyncio
async def test_a_runtime_answering_5xx_is_quarantined() -> None:
    """502 is what a dead runtime process answers. It used to be the one case
    that did *not* evict, while a healthy-but-routeless sandbox (404) did."""

    class _Runtime:
        async def post(self, url, **kwargs):
            return httpx.Response(502, request=httpx.Request("POST", url))

    dispatch, endpoint, quarantined, _ = await _invoke_and_capture_quarantine(_Runtime())

    quarantined.assert_awaited_once_with(dispatch.pod_id, endpoint)


@pytest.mark.asyncio
async def test_a_runtime_that_will_not_connect_is_quarantined() -> None:
    """Connection refused: nothing was served, so the run cannot have started."""

    class _Runtime:
        async def post(self, url, **kwargs):
            raise httpx.ConnectError("connection refused")

    dispatch, endpoint, quarantined, _ = await _invoke_and_capture_quarantine(_Runtime())

    quarantined.assert_awaited_once_with(dispatch.pod_id, endpoint)


@pytest.mark.asyncio
async def test_our_own_deadline_does_not_quarantine_the_sandbox() -> None:
    """The other half of the rule, and the one that must not regress.

    A read timeout is *our* deadline expiring, not the sandbox failing. The run
    may be executing user code right now, so the sandbox is still the right one
    and destroying it would kill work in flight.
    """

    class _Runtime:
        async def post(self, url, **kwargs):
            raise httpx.ReadTimeout("deadline exceeded")

    _, _, quarantined, invalidated = await _invoke_and_capture_quarantine(_Runtime())

    quarantined.assert_not_awaited()
    invalidated.assert_not_awaited()
