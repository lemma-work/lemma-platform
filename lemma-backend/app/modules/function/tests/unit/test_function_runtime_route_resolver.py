from __future__ import annotations

import asyncio

from sandbox_runtime.errors import SandboxUnavailable

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from sandbox_runtime.protocol import (
    SandboxProfileRef,
    AdmissionClass,
    FunctionRuntimeLease,
    RuntimeRequestHeader,
    WorkloadKind,
)

from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpointCache,
)
from app.modules.function.application.function_runtime_route_resolver import (
    FunctionRuntimeRouteResolver,
)
from app.modules.function.domain.entities import (
    FunctionDispatchMode,
    FunctionExecutionDispatch,
)


def _dispatch(
    mode: FunctionDispatchMode = FunctionDispatchMode.SYNCHRONOUS,
) -> FunctionExecutionDispatch:
    return FunctionExecutionDispatch(
        run_id=uuid4(),
        pod_id=uuid4(),
        function_id=uuid4(),
        function_name="route-test",
        user_id=uuid4(),
        user_email=None,
        config={},
        mode=mode,
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        revision_hash=f"sha256:{'a' * 64}",
        input_data={},
    )


def _lease(pod_id, *, hours: int = 5) -> FunctionRuntimeLease:
    return FunctionRuntimeLease(
        logical_id=pod_id,
        allocation_id=uuid4(),
        allocation_epoch=3,
        profile=SandboxProfileRef(
            name="function-python-v1",
            digest=f"sha256:{'2' * 64}",
        ),
        url="https://direct-runtime.e2b.example/",
        request_headers=(
            RuntimeRequestHeader(
                name="E2B-Traffic-Access-Token",
                value="provider-secret",
            ),
        ),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=hours),
    )


@pytest.mark.asyncio
async def test_execution_endpoint_ensures_leases_and_caches_direct_route() -> None:
    dispatch = _dispatch()
    client = SimpleNamespace(
        ensure_sandbox=AsyncMock(
            return_value=SimpleNamespace(ready=True, retry_after_ms=None)
        ),
        lease_function_runtime=AsyncMock(return_value=_lease(dispatch.pod_id)),
        close=AsyncMock(),
    )
    resolver = FunctionRuntimeRouteResolver(
        sandbox_client_factory=lambda: client,
        endpoint_cache=FunctionRuntimeEndpointCache(),
    )

    first = await resolver.endpoint(dispatch)
    second = await resolver.endpoint(dispatch)

    assert first == second
    assert first.url == "https://direct-runtime.e2b.example/"
    assert first.headers() == {"E2B-Traffic-Access-Token": "provider-secret"}
    client.ensure_sandbox.assert_awaited_once()
    client.lease_function_runtime.assert_awaited_once()
    ensure = client.ensure_sandbox.await_args
    assert ensure.args[:2] == (WorkloadKind.FUNCTION, dispatch.pod_id)
    assert ensure.kwargs["admission_class"] == AdmissionClass.LATENCY
    assert ensure.kwargs["verify_ready"] is True
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_control_endpoint_leases_existing_allocation_without_ensure() -> None:
    dispatch = _dispatch(FunctionDispatchMode.ASYNCHRONOUS)
    client = SimpleNamespace(
        ensure_sandbox=AsyncMock(),
        lease_function_runtime=AsyncMock(return_value=_lease(dispatch.pod_id)),
        close=AsyncMock(),
    )
    resolver = FunctionRuntimeRouteResolver(
        sandbox_client_factory=lambda: client,
        endpoint_cache=FunctionRuntimeEndpointCache(),
    )

    endpoint = await resolver.control_endpoint(dispatch)

    assert endpoint.url == "https://direct-runtime.e2b.example/"
    client.ensure_sandbox.assert_not_awaited()
    client.lease_function_runtime.assert_awaited_once()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_last_failure_survives_the_ensure_deadline() -> None:
    """A timeout says why the sandbox never came up, not just that it did not."""
    dispatch = _dispatch(FunctionDispatchMode.ASYNCHRONOUS)
    error = SandboxUnavailable(
        "provider active sandbox capacity is exhausted",
        retry_after_ms=1,
    )
    client = SimpleNamespace(
        ensure_sandbox=AsyncMock(side_effect=error),
        close=AsyncMock(),
    )
    resolver = FunctionRuntimeRouteResolver(
        sandbox_client_factory=lambda: client,
        endpoint_cache=FunctionRuntimeEndpointCache(),
    )

    with pytest.raises(SandboxUnavailable) as raised:
        await resolver._ensure_sandbox(
            client,
            dispatch.pod_id,
            admission_class=AdmissionClass.BATCH,
            deadline_at=datetime.now(timezone.utc) + timedelta(milliseconds=5),
        )

    assert "capacity is exhausted" in str(raised.value)


@pytest.mark.asyncio
async def test_quarantine_destroys_the_sandbox_not_just_the_cached_endpoint() -> None:
    """Dropping the cache alone would re-adopt the same dead sandbox.

    Adoption asks the provider whether the sandbox is running, and a sandbox
    whose runtime process has died is still running. So the reload hands back
    the same broken endpoint, which is why the development outage lasted 100
    minutes instead of one failed run.
    """
    dispatch = _dispatch()
    client = SimpleNamespace(
        ensure_sandbox=AsyncMock(
            return_value=SimpleNamespace(ready=True, retry_after_ms=None)
        ),
        lease_function_runtime=AsyncMock(return_value=_lease(dispatch.pod_id)),
        destroy_sandbox=AsyncMock(),
        close=AsyncMock(),
    )
    resolver = FunctionRuntimeRouteResolver(
        sandbox_client_factory=lambda: client,
        endpoint_cache=FunctionRuntimeEndpointCache(),
    )
    endpoint = await resolver.endpoint(dispatch)

    await resolver.quarantine(dispatch.pod_id, endpoint)

    client.destroy_sandbox.assert_awaited_once()
    assert client.destroy_sandbox.await_args.args[1] == dispatch.pod_id
    # And the next resolve provisions rather than reusing the cached entry.
    client.lease_function_runtime.reset_mock()
    await resolver.endpoint(dispatch)
    client.lease_function_runtime.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failed_destroy_still_leaves_the_endpoint_evicted() -> None:
    """Quarantine is best effort on the sandbox and definite on the cache."""
    dispatch = _dispatch()
    client = SimpleNamespace(
        ensure_sandbox=AsyncMock(
            return_value=SimpleNamespace(ready=True, retry_after_ms=None)
        ),
        lease_function_runtime=AsyncMock(return_value=_lease(dispatch.pod_id)),
        destroy_sandbox=AsyncMock(
            side_effect=SandboxUnavailable("provider unreachable")
        ),
        close=AsyncMock(),
    )
    resolver = FunctionRuntimeRouteResolver(
        sandbox_client_factory=lambda: client,
        endpoint_cache=FunctionRuntimeEndpointCache(),
    )
    endpoint = await resolver.endpoint(dispatch)

    await resolver.quarantine(dispatch.pod_id, endpoint)

    client.lease_function_runtime.reset_mock()
    await resolver.endpoint(dispatch)
    client.lease_function_runtime.assert_awaited_once()


# -- how long the poll waits, which nothing pinned -----------------------------


@pytest.mark.asyncio
async def test_the_readiness_poll_does_not_oversleep_the_sandbox(monkeypatch):
    """Waiting for a sandbox to serve is a cheap local check, so ask often.

    The ladder used to double from 500ms to a 5s ceiling: 0.5, 1, 2, 4, 5. Four
    waits was 7.5-9s of pure sleeping *on top of* however long the sandbox
    actually took, so a boot finishing at 2.1s was not noticed until 3.5s. The
    caller is a user waiting on a synchronous function call.
    """
    slept: list[float] = []

    async def _record(delay):
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _record)
    deadline = datetime.now(timezone.utc) + timedelta(seconds=120)

    for attempt in range(6):
        await FunctionRuntimeRouteResolver._wait_retry(None, deadline, attempt=attempt)

    # Six polls cap at 0.1+0.2+0.4+0.75+0.75+0.75 = 2.95s, times 1.2 jitter.
    # The old ladder capped at 0.5+1+2+4+5+5 = 17.5s, times the same jitter.
    assert sum(slept) < 4.0, (
        f"six polls slept {sum(slept):.2f}s in total; the old ladder spent "
        "over 20s here"
    )
    assert max(slept) <= 0.75 * 1.2 + 1e-9, "the ceiling is 750ms plus jitter"


@pytest.mark.asyncio
async def test_a_provider_retry_after_still_overrides_the_ceiling(monkeypatch):
    """Polling faster must not mean ignoring a sandbox that said 'not yet'."""
    slept: list[float] = []

    async def _record(delay):
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _record)
    deadline = datetime.now(timezone.utc) + timedelta(seconds=120)

    await FunctionRuntimeRouteResolver._wait_retry(5_000, deadline, attempt=0)

    assert slept[0] >= 5.0
