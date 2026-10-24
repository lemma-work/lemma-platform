"""The `/workspace/*` routes still answer, in their old shapes, and say so.

They are `/machines/me` under the old paths: published apps, functions and
scripts written against them keep working until the sunset, and every
response names the replacement.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.api.deprecation import DEPRECATION_HEADER
from app.modules.workspace.api.controllers import (
    workspace_shims_controller as shims,
)
from app.modules.workspace.config import workspace_settings
from sandbox_runtime.protocol import (
    PortAccessGrant,
    PortProtocol,
    SandboxKey,
    WorkloadKind,
)


def _app(machine_id) -> FastAPI:
    app = FastAPI()
    app.include_router(shims.router)
    app.include_router(shims.apps_router)
    resolved = SimpleNamespace(machine=SimpleNamespace(id=machine_id))
    app.dependency_overrides[shims.own_machine] = lambda: resolved
    app.dependency_overrides[shims.own_machine_status] = lambda: resolved
    return app


@pytest.mark.asyncio
async def test_the_old_browser_access_answers_in_its_old_shape_on_the_own_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine_id = uuid4()
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
                url="https://sandbox.test/public/machine-ports/signed/",
                expires_at=expires_at,
            )

    monkeypatch.setattr(workspace_settings, "runtime_credential_key", "k" * 32)
    monkeypatch.setattr(
        "app.modules.workspace.services.workspace_sandbox_service.WorkspaceSandboxService",
        FakeService,
    )
    async with AsyncClient(
        transport=ASGITransport(app=_app(machine_id)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/workspace/apps/browser/access", json={"ttl_seconds": 900}
        )

    assert response.status_code == 200
    assert response.json()["app"] == "browser"
    assert response.json()["url"].endswith("/public/machine-ports/signed/")
    assert calls == [(machine_id, 900)]
    assert "POST /machines/me/browser:access" in response.headers[DEPRECATION_HEADER]
    assert response.headers["Deprecation"] == "true"


def test_every_old_route_is_marked_deprecated_with_its_replacement() -> None:
    app = _app(uuid4())
    spec = app.openapi()
    old = {
        (path, method): operation
        for path, operations in spec["paths"].items()
        for method, operation in operations.items()
    }
    assert old, "the shims are registered"
    for (path, method), operation in old.items():
        assert operation.get("deprecated") is True, f"{method} {path}"
        assert operation["x-lemma-replacement"].split()[1].startswith("/machines/me")
