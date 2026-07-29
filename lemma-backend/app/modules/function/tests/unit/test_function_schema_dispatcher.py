from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from agentbox_client import PortAccessGrant, SandboxHandle
from agentbox_client.models import PortProtocol, ProfileRef, WorkloadKind

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


@pytest.mark.asyncio
async def test_schema_inspection_uses_pod_function_runtime_without_workspace(
    monkeypatch,
) -> None:
    pod_id = uuid4()
    function_id = uuid4()
    revision_hash = f"sha256:{'a' * 64}"
    user_id = uuid4()
    observed: dict[str, object] = {}

    class _AgentBoxClient:
        async def ensure_sandbox(self, kind, logical_id, **_kwargs):
            observed["sandbox_key"] = (kind, logical_id)
            return SandboxHandle(
                workload_kind=kind,
                logical_id=logical_id,
                desired_state="present",
                profile=ProfileRef(
                    name="function-python-v1",
                    digest=f"sha256:{'2' * 64}",
                ),
                allocation_state="active",
                allocation_id=uuid4(),
                allocation_epoch=1,
                ready=True,
                operation_id=None,
                retry_after_ms=None,
            )

        async def create_port_access(self, kind, logical_id, port, **_kwargs):
            observed["port_key"] = (kind, logical_id, port)
            return PortAccessGrant(
                workload_kind=kind,
                logical_id=logical_id,
                port=port,
                protocol=PortProtocol.HTTP,
                url="https://agentbox.test/port-access/signed/",
                expires_at=_kwargs["expires_at"],
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
        agentbox_client_factory=_AgentBoxClient,
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
    assert observed["port_key"] == (WorkloadKind.FUNCTION, pod_id, 8090)
    assert str(observed["url"]).endswith(f"/functions/{function_id}/schemas")
    headers = observed["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer delegated-function-token"
    assert headers["If-Match"] == f'"{revision_hash}"'
    token_claims = observed["token_claims"]
    assert isinstance(token_claims, dict)
    assert token_claims["user_id"] == user_id
    assert token_claims["workload_id"] == function_id
    assert token_claims["workload_name"] == "inspect_me"
