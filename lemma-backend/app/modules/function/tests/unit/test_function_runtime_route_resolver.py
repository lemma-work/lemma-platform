from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from agentbox_client import AdmissionClass, WorkloadKind

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


@pytest.mark.asyncio
async def test_execution_endpoint_ensures_once_and_caches_exact_grant() -> None:
    dispatch = _dispatch()
    grant = SimpleNamespace(
        url="https://agentbox.test/exact/",
        expires_at=dispatch.deadline_at,
    )
    client = SimpleNamespace(
        ensure_sandbox=AsyncMock(
            return_value=SimpleNamespace(ready=True, retry_after_ms=None)
        ),
        create_port_access=AsyncMock(return_value=grant),
        close=AsyncMock(),
    )
    resolver = FunctionRuntimeRouteResolver(
        agentbox_client_factory=lambda: client,
        endpoint_cache=FunctionRuntimeEndpointCache(),
    )

    first = await resolver.endpoint(dispatch)
    second = await resolver.endpoint(dispatch)

    assert first == second
    client.ensure_sandbox.assert_awaited_once()
    ensure = client.ensure_sandbox.await_args
    assert ensure.args[:2] == (WorkloadKind.FUNCTION, dispatch.pod_id)
    assert ensure.kwargs["admission_class"] == AdmissionClass.LATENCY
    client.create_port_access.assert_awaited_once()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_control_endpoint_never_creates_or_ensures_a_sandbox() -> None:
    dispatch = _dispatch(FunctionDispatchMode.ASYNCHRONOUS)
    client = SimpleNamespace(
        ensure_sandbox=AsyncMock(
            side_effect=AssertionError("control path must not ensure a sandbox")
        ),
        create_port_access=AsyncMock(
            return_value=SimpleNamespace(
                url="https://agentbox.test/control/",
                expires_at=dispatch.deadline_at,
            )
        ),
        close=AsyncMock(),
    )
    resolver = FunctionRuntimeRouteResolver(
        agentbox_client_factory=lambda: client,
        endpoint_cache=FunctionRuntimeEndpointCache(),
    )

    endpoint = await resolver.control_endpoint(dispatch)

    assert endpoint.url == "https://agentbox.test/control/"
    client.ensure_sandbox.assert_not_awaited()
    client.create_port_access.assert_awaited_once()
    client.close.assert_awaited_once()
