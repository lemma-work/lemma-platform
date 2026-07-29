from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import httpx
from agentbox_client import AdmissionClass, AgentBoxApiError, WorkloadKind
from agentbox_client.models import (
    AgentBoxErrorBody,
    AgentBoxErrorResponse,
    RetryDisposition,
)

from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpointCache,
)
from app.modules.function.application.function_runtime_route_resolver import (
    FunctionRuntimeRouteResolver,
    trusted_function_runtime_headers,
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
async def test_execution_endpoint_ensures_once_and_caches_trusted_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.function.application.function_runtime_route_resolver.settings."
        "agentbox_api_url",
        "https://agentbox.test",
    )
    monkeypatch.setattr(
        "app.modules.function.application.function_runtime_route_resolver.settings."
        "agentbox_api_key",
        "manager-secret",
    )
    dispatch = _dispatch()
    client = SimpleNamespace(
        ensure_sandbox=AsyncMock(
            return_value=SimpleNamespace(ready=True, retry_after_ms=None)
        ),
        close=AsyncMock(),
    )
    resolver = FunctionRuntimeRouteResolver(
        agentbox_client_factory=lambda: client,
        endpoint_cache=FunctionRuntimeEndpointCache(),
    )

    first = await resolver.endpoint(dispatch)
    second = await resolver.endpoint(dispatch)

    assert first == second
    assert first.url == (
        f"https://agentbox.test/trusted/function-runtimes/{dispatch.pod_id}/"
    )
    assert trusted_function_runtime_headers() == {"X-API-Key": "manager-secret"}
    client.ensure_sandbox.assert_awaited_once()
    ensure = client.ensure_sandbox.await_args
    assert ensure.args[:2] == (WorkloadKind.FUNCTION, dispatch.pod_id)
    assert ensure.kwargs["admission_class"] == AdmissionClass.LATENCY
    assert ensure.kwargs["verify_ready"] is True
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_control_endpoint_never_creates_or_ensures_a_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.function.application.function_runtime_route_resolver.settings."
        "agentbox_api_url",
        "https://agentbox.test",
    )
    dispatch = _dispatch(FunctionDispatchMode.ASYNCHRONOUS)
    resolver = FunctionRuntimeRouteResolver(
        agentbox_client_factory=lambda: pytest.fail(
            "control path must not construct an AgentBox client"
        ),
        endpoint_cache=FunctionRuntimeEndpointCache(),
    )

    endpoint = await resolver.control_endpoint(dispatch)

    assert endpoint.url == (
        f"https://agentbox.test/trusted/function-runtimes/{dispatch.pod_id}/"
    )


@pytest.mark.asyncio
async def test_capacity_exhaustion_survives_ensure_deadline() -> None:
    dispatch = _dispatch(FunctionDispatchMode.ASYNCHRONOUS)
    error = AgentBoxApiError(
        httpx.Response(429),
        AgentBoxErrorResponse(
            error=AgentBoxErrorBody(
                code="CAPACITY_EXHAUSTED",
                message="provider active sandbox capacity is exhausted",
                retry=RetryDisposition.WAIT,
                retry_after_ms=1,
            )
        ),
    )
    client = SimpleNamespace(
        ensure_sandbox=AsyncMock(side_effect=error),
        close=AsyncMock(),
    )
    resolver = FunctionRuntimeRouteResolver(
        agentbox_client_factory=lambda: client,
        endpoint_cache=FunctionRuntimeEndpointCache(),
    )

    with pytest.raises(AgentBoxApiError) as raised:
        await resolver._ensure_sandbox(
            client,
            dispatch,
            deadline_at=datetime.now(timezone.utc) + timedelta(milliseconds=5),
        )

    assert raised.value.code == "CAPACITY_EXHAUSTED"
