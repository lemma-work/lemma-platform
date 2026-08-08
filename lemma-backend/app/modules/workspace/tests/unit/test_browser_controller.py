from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from sandbox_runtime.protocol import SandboxKey, PortAccessGrant, PortProtocol, WorkloadKind
from app.modules.workspace.config import workspace_settings
from app.core.api.dependencies import get_current_user
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.workspace.api.controllers import browser_controller
from app.modules.workspace.api.controllers.browser_controller import router


@pytest.mark.asyncio
async def test_workspace_browser_access_uses_canonical_signed_port_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    calls: list[tuple] = []
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)

    class FakeService:
        async def create_browser_access(self, logical_id, *, ttl_seconds):
            calls.append((logical_id, ttl_seconds))
            return PortAccessGrant(
                key=SandboxKey(
                    workload_kind=WorkloadKind.WORKSPACE, logical_id=logical_id
                ),
                port=4848,
                protocol=PortProtocol.HTTP,
                url="https://agentbox.test/port-access/signed/",
                expires_at=expires_at,
            )

    monkeypatch.setattr(
        workspace_settings, "runtime_credential_key", "k" * 32
    )
    monkeypatch.setattr(browser_controller, "WorkspaceSandboxService", FakeService)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: UserEntity(
        id=user_id, email="test@example.com"
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/workspace/apps/browser/access", json={"ttl_seconds": 900}
        )

    assert response.status_code == 200
    assert response.json()["app"] == "browser"
    assert response.json()["url"] == "https://agentbox.test/port-access/signed/"
    assert calls == [(user_id, 900)]


def test_workspace_browser_access_route_is_in_openapi() -> None:
    app = FastAPI()
    app.include_router(router)
    assert "/workspace/apps/browser/access" in app.openapi()["paths"]
