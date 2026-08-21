from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from sandbox_runtime.protocol import (
    AllocationState,
    SandboxDesiredState,
    SandboxKey,
    SandboxProfileRef,
    FunctionRuntimeLease,
    RuntimeRequestHeader,
    SandboxHandle,
)
from sandbox_runtime.protocol import WorkloadKind

from app.modules.function.application.function_session_token_cache import (
    FunctionSessionToken,
    FunctionSessionTokenCache,
)
from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpointCache,
)
from app.modules.function.application.function_schema_dispatcher import (
    FunctionSchemaDispatcher,
)
from app.modules.function.domain.entities import FunctionArtifact
from app.modules.function.domain.errors import FunctionValidationError


@pytest.mark.asyncio
async def test_schema_inspection_uses_pod_function_runtime_without_workspace(
    monkeypatch,
) -> None:
    pod_id = uuid4()
    function_id = uuid4()
    revision_hash = f"sha256:{'a' * 64}"
    user_id = uuid4()
    observed: dict[str, object] = {}

    class _LocalSandboxClient:
        async def ensure_sandbox(self, kind, logical_id, **_kwargs):
            observed["sandbox_key"] = (kind, logical_id)
            return SandboxHandle(
                key=SandboxKey(workload_kind=kind, logical_id=logical_id),
                desired_state=SandboxDesiredState.PRESENT,
                profile=SandboxProfileRef(
                    name="function-python-v1",
                    digest=f"sha256:{'2' * 64}",
                ),
                allocation_state=AllocationState.ACTIVE,
                allocation_id=uuid4(),
                allocation_epoch=1,
                ready=True,
                operation_id=None,
                retry_after_ms=None,
            )

        async def lease_function_runtime(
            self,
            logical_id,
            *,
            required_valid_until,
            deadline_at,
        ):
            del deadline_at
            return FunctionRuntimeLease(
                logical_id=logical_id,
                allocation_id=uuid4(),
                allocation_epoch=1,
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
                expires_at=required_valid_until + timedelta(minutes=1),
            )

        async def close(self):
            return None

    class _RuntimeClient:
        async def post(self, url, *, headers, timeout):
            observed.update(url=url, headers=headers, timeout=timeout)
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "ok": True,
                    "schemas": {
                        "input": {"type": "object"},
                        "output": {"type": "object"},
                        "config": None,
                    },
                    "error": None,
                },
            )

    monkeypatch.setattr(
        "app.modules.function.application.function_schema_dispatcher.settings."
        "function_runtime_gateway_url",
        "http://127.0.0.1:8711",
    )

    async def mint_token(**kwargs) -> FunctionSessionToken:
        observed["token_claims"] = kwargs
        return FunctionSessionToken(
            value="delegated-function-token",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    dispatcher = FunctionSchemaDispatcher(
        sandbox_client_factory=_LocalSandboxClient,
        token_minter=mint_token,
        token_cache=FunctionSessionTokenCache(),
        endpoint_cache=FunctionRuntimeEndpointCache(),
        runtime_http_client_factory=_RuntimeClient,
        delegated_tokens_enabled=True,
    )

    schemas = await dispatcher.extract_schemas(
        function_id=function_id,
        pod_id=pod_id,
        user_id=user_id,
        function_name="inspect_me",
        artifact=FunctionArtifact(revision_hash=revision_hash),
    )

    assert schemas.input == {"type": "object"}
    assert observed["sandbox_key"] == (WorkloadKind.FUNCTION, pod_id)
    assert "port_key" not in observed
    assert str(observed["url"]).endswith(f"/functions/{function_id}/schemas")
    headers = observed["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer delegated-function-token"
    assert headers["E2B-Traffic-Access-Token"] == "provider-secret"
    assert "X-API-Key" not in headers
    assert headers["If-Match"] == f'"{revision_hash}"'
    token_claims = observed["token_claims"]
    assert isinstance(token_claims, dict)
    assert token_claims["user_id"] == user_id
    assert token_claims["workload_id"] == function_id
    assert token_claims["workload_name"] == "inspect_me"


class _StubSandboxClient:
    """A minimal ``sandbox_client_factory`` collaborator: enough to satisfy the
    route resolver's ensure+lease sequence on every call, including retries."""

    async def ensure_sandbox(self, kind, logical_id, **_kwargs):
        return SandboxHandle(
            key=SandboxKey(workload_kind=kind, logical_id=logical_id),
            desired_state=SandboxDesiredState.PRESENT,
            profile=SandboxProfileRef(
                name="function-python-v1",
                digest=f"sha256:{'2' * 64}",
            ),
            allocation_state=AllocationState.ACTIVE,
            allocation_id=uuid4(),
            allocation_epoch=1,
            ready=True,
            operation_id=None,
            retry_after_ms=None,
        )

    async def lease_function_runtime(
        self, logical_id, *, required_valid_until, deadline_at
    ):
        del deadline_at
        return FunctionRuntimeLease(
            logical_id=logical_id,
            allocation_id=uuid4(),
            allocation_epoch=1,
            profile=SandboxProfileRef(
                name="function-python-v1",
                digest=f"sha256:{'2' * 64}",
            ),
            url="https://direct-runtime.e2b.example/",
            request_headers=(),
            expires_at=required_valid_until + timedelta(minutes=1),
        )

    async def close(self):
        return None


async def _mint_token(**_kwargs) -> FunctionSessionToken:
    return FunctionSessionToken(
        value="delegated-function-token",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def _dispatcher(runtime_client) -> FunctionSchemaDispatcher:
    return FunctionSchemaDispatcher(
        sandbox_client_factory=_StubSandboxClient,
        token_minter=_mint_token,
        token_cache=FunctionSessionTokenCache(),
        endpoint_cache=FunctionRuntimeEndpointCache(),
        runtime_http_client_factory=lambda: runtime_client,
        delegated_tokens_enabled=True,
    )


def _ok_response(url: str) -> httpx.Response:
    return httpx.Response(
        200,
        request=httpx.Request("POST", url),
        json={
            "ok": True,
            "schemas": {"input": {"type": "object"}, "output": {"type": "object"}},
            "error": None,
        },
    )


@pytest.mark.asyncio
async def test_schema_dispatcher_retries_once_after_a_transport_error() -> None:
    """A dropped connection on the first attempt must not fail the whole
    inspection: the endpoint is invalidated and one retry gets a fresh lease."""
    calls: list[str] = []

    class _RuntimeClient:
        async def post(self, url, *, headers, timeout):
            del headers, timeout
            calls.append(url)
            if len(calls) == 1:
                raise httpx.ConnectError("connection reset", request=None)
            return _ok_response(url)

    dispatcher = _dispatcher(_RuntimeClient())

    schemas = await dispatcher.extract_schemas(
        function_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        function_name="inspect_me",
        artifact=FunctionArtifact(revision_hash=f"sha256:{'a' * 64}"),
    )

    assert schemas.input == {"type": "object"}
    assert len(calls) == 2


@pytest.mark.parametrize("status_code", [404, 410, 500, 503])
@pytest.mark.asyncio
async def test_schema_dispatcher_retries_once_after_a_stale_or_dead_endpoint(
    status_code: int,
) -> None:
    """A 404/410 (the resident runtime moved) or a 5xx (it is unhealthy) both
    mean this endpoint is no longer good — invalidate it and retry once rather
    than surfacing a transient routing error to the caller."""
    calls: list[str] = []

    class _RuntimeClient:
        async def post(self, url, *, headers, timeout):
            del headers, timeout
            calls.append(url)
            if len(calls) == 1:
                return httpx.Response(status_code, request=httpx.Request("POST", url))
            return _ok_response(url)

    dispatcher = _dispatcher(_RuntimeClient())

    schemas = await dispatcher.extract_schemas(
        function_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        function_name="inspect_me",
        artifact=FunctionArtifact(revision_hash=f"sha256:{'a' * 64}"),
    )

    assert schemas.input == {"type": "object"}
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_schema_dispatcher_rejects_without_retry_on_an_ordinary_error_status() -> (
    None
):
    """A status outside the retry set (not a transport error, not 404/410/5xx)
    is a definitive rejection of this artifact -- retrying would not help, so
    exactly one call should happen."""
    calls: list[str] = []

    class _RuntimeClient:
        async def post(self, url, *, headers, timeout):
            del headers, timeout
            calls.append(url)
            return httpx.Response(
                403,
                request=httpx.Request("POST", url),
                json={"detail": "forbidden"},
            )

    dispatcher = _dispatcher(_RuntimeClient())

    with pytest.raises(FunctionValidationError, match="rejected the artifact"):
        await dispatcher.extract_schemas(
            function_id=uuid4(),
            pod_id=uuid4(),
            user_id=uuid4(),
            function_name="inspect_me",
            artifact=FunctionArtifact(revision_hash=f"sha256:{'a' * 64}"),
        )

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_schema_dispatcher_rejects_an_unparseable_response_body() -> None:
    class _RuntimeClient:
        async def post(self, url, *, headers, timeout):
            del headers, timeout
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                content=b"not json",
            )

    dispatcher = _dispatcher(_RuntimeClient())

    with pytest.raises(FunctionValidationError, match="invalid response"):
        await dispatcher.extract_schemas(
            function_id=uuid4(),
            pod_id=uuid4(),
            user_id=uuid4(),
            function_name="inspect_me",
            artifact=FunctionArtifact(revision_hash=f"sha256:{'a' * 64}"),
        )


@pytest.mark.asyncio
async def test_schema_dispatcher_rejects_when_inspection_reports_failure() -> None:
    """A 200 whose body says ``ok: false`` (the sandbox ran the inspection but
    the function's own code failed to import/parse) must surface the sandbox's
    error, not be treated as a successful extraction."""

    class _RuntimeClient:
        async def post(self, url, *, headers, timeout):
            del headers, timeout
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "ok": False,
                    "schemas": None,
                    "error": {"name": "TypeError", "message": "bad annotation"},
                },
            )

    dispatcher = _dispatcher(_RuntimeClient())

    with pytest.raises(FunctionValidationError, match="TypeError: bad annotation"):
        await dispatcher.extract_schemas(
            function_id=uuid4(),
            pod_id=uuid4(),
            user_id=uuid4(),
            function_name="inspect_me",
            artifact=FunctionArtifact(revision_hash=f"sha256:{'a' * 64}"),
        )
