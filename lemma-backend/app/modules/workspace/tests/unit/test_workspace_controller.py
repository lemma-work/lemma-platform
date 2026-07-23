from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from agentbox_client import PortAccessGrant, PortProtocol, WorkloadKind
from app.core.api.dependencies import get_current_user
from app.core.config import settings
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.workspace.api.controllers import workspace_controller
from app.modules.workspace.api.controllers.workspace_controller import router
from app.modules.workspace.contracts import SandboxInfo
from app.modules.workspace.services.workspace_activity_store import WorkspaceActivity


@pytest.mark.asyncio
async def test_workspace_me_returns_typed_sandbox_session_and_browser_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    pod_id = uuid4()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    class ActivityStore:
        async def get_activity(self, **_kwargs):
            return WorkspaceActivity(
                user_id=user_id,
                runtime="agentbox",
                last_used_at=datetime.now(timezone.utc),
                pod_id=pod_id,
                session_id="shell-session",
            )

    class FakeService:
        @staticmethod
        def _resolve_runtime() -> str:
            return "agentbox"

        async def get_or_create_sandbox(self, logical_id):
            assert logical_id == user_id
            return SandboxInfo(
                sandbox_id=str(user_id),
                name=str(user_id),
                namespace=None,
                status="RUNNING",
                image="",
                endpoint=f"agentbox://{user_id}",
            )

        async def create_browser_access(
            self, logical_id, *, ttl_seconds, ensure_sandbox
        ):
            assert (logical_id, ttl_seconds, ensure_sandbox) == (
                user_id,
                600,
                False,
            )
            return PortAccessGrant(
                workload_kind=WorkloadKind.WORKSPACE,
                logical_id=user_id,
                port=4848,
                protocol=PortProtocol.HTTP,
                url="https://agentbox.test/port-access/signed/",
                expires_at=expires_at,
            )

    monkeypatch.setattr(settings, "agentbox_api_key", "test-key")
    monkeypatch.setattr(workspace_controller, "WorkspaceSandboxService", FakeService)
    monkeypatch.setattr(
        workspace_controller, "get_workspace_activity_store", ActivityStore
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: UserEntity(
        id=user_id, email="test@example.com"
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/workspace/me")

    assert response.status_code == 200
    body = response.json()
    assert body["sandbox"]["id"] == str(user_id)
    assert body["sandbox"]["ready"] is True
    assert body["active_session"]["session_id"] == "shell-session"
    assert body["apps"]["browser"]["url"].endswith("/port-access/signed/")


def test_workspace_me_route_is_in_openapi() -> None:
    app = FastAPI()
    app.include_router(router)
    assert "/workspace/me" in app.openapi()["paths"]
