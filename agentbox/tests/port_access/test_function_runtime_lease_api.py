from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import FastAPI
import httpx
import pytest

from agentbox.api.fabric import router
from agentbox.config import settings
from agentbox.domain import SandboxKey, SandboxProfileRef, WorkloadKind
from agentbox.port_access import FunctionRuntimeEndpointLease
from agentbox.ports import ProviderMetadataEntry


pytestmark = pytest.mark.asyncio


class _FunctionRuntimeLeaseService:
    def __init__(self) -> None:
        self.logical_ids: list[UUID] = []
        self.deadlines: list[datetime] = []
        self.required_valid_until: list[datetime | None] = []

    async def lease_function_runtime(
        self,
        logical_id: UUID,
        *,
        deadline_at: datetime,
        required_valid_until: datetime | None = None,
    ) -> FunctionRuntimeEndpointLease:
        self.logical_ids.append(logical_id)
        self.deadlines.append(deadline_at)
        self.required_valid_until.append(required_valid_until)
        return FunctionRuntimeEndpointLease(
            key=SandboxKey(WorkloadKind.FUNCTION, logical_id),
            allocation_id=uuid4(),
            allocation_epoch=3,
            profile=SandboxProfileRef(
                name="function-python-v1",
                digest=f"sha256:{'a' * 64}",
            ),
            url="https://runtime.test/",
            request_headers=(
                ProviderMetadataEntry(
                    name="E2B-Traffic-Access-Token",
                    value="provider-secret",
                ),
            ),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )


async def test_runtime_lease_requires_manager_key_and_is_never_cacheable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "agentbox_api_key", "manager-secret")
    logical_id = uuid4()
    deadline_at = datetime.now(timezone.utc) + timedelta(seconds=20)
    required_valid_until = datetime.now(timezone.utc) + timedelta(minutes=2)
    service = _FunctionRuntimeLeaseService()
    app = FastAPI()
    app.state.port_access = service
    app.include_router(router)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://agentbox.test",
    )
    try:
        denied = await client.post(
            f"/sandboxes/function/{logical_id}/runtime:lease",
            json={
                "required_valid_until": required_valid_until.isoformat(),
                "deadline_at": deadline_at.isoformat(),
            },
        )
        allowed = await client.post(
            f"/sandboxes/function/{logical_id}/runtime:lease",
            headers={"X-API-Key": "manager-secret"},
            json={
                "required_valid_until": required_valid_until.isoformat(),
                "deadline_at": deadline_at.isoformat(),
            },
        )
    finally:
        await client.aclose()

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.headers["Cache-Control"] == "no-store"
    assert service.logical_ids == [logical_id]
    assert service.deadlines == [deadline_at]
    assert service.required_valid_until == [required_valid_until]
    body = allowed.json()
    assert body["url"] == "https://runtime.test/"
    assert body["allocation_epoch"] == 3
    assert body["request_headers"] == [
        {
            "name": "E2B-Traffic-Access-Token",
            "value": "provider-secret",
        }
    ]
    assert "provider-secret" not in repr(
        FunctionRuntimeEndpointLease(
            key=SandboxKey(WorkloadKind.FUNCTION, logical_id),
            allocation_id=UUID(body["allocation_id"]),
            allocation_epoch=body["allocation_epoch"],
            profile=SandboxProfileRef(
                name=body["profile"]["name"],
                digest=body["profile"]["digest"],
            ),
            url=body["url"],
            request_headers=(
                ProviderMetadataEntry(
                    name=body["request_headers"][0]["name"],
                    value=body["request_headers"][0]["value"],
                ),
            ),
            expires_at=datetime.fromisoformat(body["expires_at"]),
        )
    )
