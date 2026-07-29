from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from fastapi import FastAPI
import httpx
import pytest

from agentbox.api.port_proxy import access_router
from agentbox.config import settings
from agentbox.ports import ProviderMetadataEntry, ProviderPortTarget


pytestmark = pytest.mark.asyncio


class _TrustedRuntimeService:
    def __init__(self) -> None:
        self.logical_ids: list[UUID] = []
        self.activity_until: list[datetime | None] = []

    async def resolve_trusted_function(
        self,
        logical_id: UUID,
        *,
        deadline_at: datetime,
        activity_until: datetime | None = None,
    ) -> ProviderPortTarget:
        del deadline_at
        self.logical_ids.append(logical_id)
        self.activity_until.append(activity_until)
        return ProviderPortTarget(
            base_url="https://runtime.test",
            headers=(
                ProviderMetadataEntry(
                    name="E2B-Traffic-Access-Token",
                    value="provider-secret",
                ),
            ),
        )


class _ResponseStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'{"ready":true}'


async def test_trusted_function_proxy_requires_manager_key_and_strips_it_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "agentbox_api_key", "manager-secret")
    logical_id = uuid4()
    activity_until = datetime.now().astimezone()
    service = _TrustedRuntimeService()
    upstream_requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=_ResponseStream(),
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = FastAPI()
    app.state.port_access = service
    app.state.port_proxy_http_client = upstream_client
    app.include_router(access_router)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://agentbox.test",
    )
    try:
        denied = await client.get(f"/trusted/function-runtimes/{logical_id}/healthz")
        allowed = await client.post(
            f"/trusted/function-runtimes/{logical_id}/functions/run",
            headers={
                "X-API-Key": "manager-secret",
                "X-AgentBox-Activity-Until": activity_until.isoformat(),
                "Authorization": "Bearer function-session",
            },
            json={"input": {}},
        )
    finally:
        await client.aclose()
        await upstream_client.aclose()

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert service.logical_ids == [logical_id]
    assert service.activity_until == [activity_until]
    assert len(upstream_requests) == 1
    forwarded = upstream_requests[0]
    assert forwarded.url == "https://runtime.test/functions/run"
    assert "x-api-key" not in forwarded.headers
    assert "x-agentbox-activity-until" not in forwarded.headers
    assert forwarded.headers["authorization"] == "Bearer function-session"
    assert forwarded.headers["e2b-traffic-access-token"] == "provider-secret"
