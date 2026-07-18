from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.api.dependencies import get_current_user
from app.core.config import settings
from app.modules.identity.domain.user_entities import UserEntity
from agentbox_client.generated.manager.models import (
    AppAccessResponse,
    SandboxSummary,
)
from app.modules.workspace.api.controllers import workspace_controller
from app.modules.workspace.api.controllers.workspace_controller import router
from app.modules.workspace.services.workspace_activity_store import WorkspaceActivity


class _FakeActivityStore:
    def __init__(self, activity: WorkspaceActivity | None):
        self.activity = activity
        self.calls: list[dict] = []

    async def get_activity(self, **kwargs):
        self.calls.append(kwargs)
        return self.activity


class _FakeStateStore:
    def __init__(self):
        self.mark_running_calls: list[dict] = []

    async def mark_running(self, **kwargs):
        self.mark_running_calls.append(kwargs)


@pytest.mark.asyncio
async def test_workspace_me_returns_sandbox_session_and_browser_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    session_pod_id = uuid4()
    sandbox_id = user_id.hex
    calls: list[dict] = []
    activity_store = _FakeActivityStore(
        WorkspaceActivity(
            user_id=user_id,
            runtime="docker",
            last_used_at=datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
            pod_id=session_pod_id,
            session_id="shell-session",
        )
    )
    state_store = _FakeStateStore()

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

        async def ensure_sandbox(self, sandbox_id_arg: str, **kwargs):
            calls.append({"ensure_sandbox": sandbox_id_arg, **kwargs})
            return SandboxSummary(
                id=sandbox_id_arg,
                ready=True,
                status="RUNNING",
            )

        async def get_app_access_url(self, sandbox_id_arg: str, app_name: str, **kwargs):
            calls.append(
                {
                    "sandbox_id": sandbox_id_arg,
                    "app_name": app_name,
                    **kwargs,
                }
            )
            return AppAccessResponse(
                sandbox_id=sandbox_id_arg,
                app=app_name,
                url=f"http://browser-{sandbox_id_arg}.localhost:8711/?token=test",
                expires_at=1_780_000_000,
            )

        async def close(self):
            calls.append({"closed": True})

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: UserEntity(
        id=user_id,
        email="test@example.com",
    )
    monkeypatch.setattr(workspace_controller, "AgentBoxClient", FakeAgentBoxClient)
    monkeypatch.setattr(
        workspace_controller,
        "get_workspace_activity_store",
        lambda: activity_store,
    )
    monkeypatch.setattr(
        workspace_controller,
        "get_workspace_state_store",
        lambda: state_store,
    )
    monkeypatch.setattr(settings, "agentbox_api_key", "manager-key")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/workspace/me")

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == str(user_id)
    assert body["sandbox"]["id"] == sandbox_id
    assert body["sandbox"]["status"] == "RUNNING"
    assert "pod_name" not in body["sandbox"]
    assert "namespace" not in body["sandbox"]
    assert "phase" not in body["sandbox"]
    assert body["active_session"]["session_id"] == "shell-session"
    assert body["active_session"]["pod_id"] == str(session_pod_id)
    assert body["apps"]["browser"]["url"].startswith(
        f"http://browser-{sandbox_id}.localhost:8711/"
    )
    assert calls[1]["ensure_sandbox"] == sandbox_id
    assert calls[1]["env"]["LEMMA_BASE_URL"]
    assert calls[2]["app_name"] == "browser"
    assert calls[2]["ttl_seconds"] == 600
    assert calls[-1] == {"closed": True}
    assert state_store.mark_running_calls[0]["user_id"] == user_id
    assert state_store.mark_running_calls[0]["pod_name"] is None


def test_workspace_me_route_is_in_openapi() -> None:
    app = FastAPI()
    app.include_router(router)

    schema = app.openapi()

    assert "/workspace/me" in schema["paths"]
