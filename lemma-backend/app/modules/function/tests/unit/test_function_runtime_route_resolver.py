from __future__ import annotations

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
