from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.api.dependencies import get_current_user
from app.modules.identity.domain.user_entities import UserEntity
from agentbox_client.generated.manager.models import AppAccessResponse
from app.modules.workspace.api.controllers import browser_controller
from app.modules.workspace.api.controllers.browser_controller import router


@pytest.mark.asyncio
async def test_workspace_browser_access_returns_manager_app_url(monkeypatch) -> None:
    user_id = uuid4()
    calls: list[dict] = []

    class FakeAgentBoxClient:
        def __init__(
            self,
            *,
            base_url: str,
            api_key: str,
            timeout_seconds: float,
            context_headers_provider=None,
        ):
            assert callable(context_headers_provider)
            calls.append(
                {
                    "base_url": base_url,
                    "api_key": api_key,
                    "timeout_seconds": timeout_seconds,
                }
            )

        async def get_app_access_url(self, sandbox_id: str, app_name: str, **kwargs):
            calls.append(
                {
                    "sandbox_id": sandbox_id,
                    "app_name": app_name,
                    **kwargs,
                }
            )
            return AppAccessResponse(
                sandbox_id=sandbox_id,
                app=app_name,
                url=f"http://browser-{sandbox_id}.localhost:8711/?token=test",
                expires_at=1_780_000_000,
            )

        async def ensure_sandbox(self, sandbox_id: str, **kwargs):
            calls.append({"ensure_sandbox": sandbox_id, **kwargs})

        async def close(self):
            calls.append({"closed": True})

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: UserEntity(
        id=user_id,
        email="test@example.com",
    )
    monkeypatch.setattr(browser_controller, "AgentBoxClient", FakeAgentBoxClient)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/workspace/apps/browser/access",
            json={
                "ttl_seconds": 600,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["app"] == "browser"
    assert body["url"].startswith(f"http://browser-{user_id.hex}.localhost:8711/")
    assert body["expires_at"] == datetime.fromtimestamp(
        1_780_000_000,
        tz=timezone.utc,
    ).isoformat().replace("+00:00", "Z")
    assert calls[1]["ensure_sandbox"] == user_id.hex
    assert calls[1]["env"]["LEMMA_BASE_URL"]
    assert calls[2]["sandbox_id"] == user_id.hex
    assert calls[2]["app_name"] == "browser"
    assert calls[2]["ttl_seconds"] == 600
    assert calls[-1] == {"closed": True}


def test_workspace_browser_access_route_is_in_openapi() -> None:
    app = FastAPI()
    app.include_router(router)

    schema = app.openapi()

    assert "/workspace/apps/browser/access" in schema["paths"]
