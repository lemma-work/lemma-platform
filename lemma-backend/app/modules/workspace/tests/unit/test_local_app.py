from __future__ import annotations

import base64
import importlib
import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.routing import Mount


os.environ.setdefault("AGENTBOX_API_KEY", "embedded-agentbox-test-key")
os.environ.setdefault(
    "AGENTBOX_API_URL", "http://127.0.0.1:8711/internal/agentbox"
)
os.environ.setdefault("AGENTBOX_PROVIDER", "docker")
os.environ.setdefault(
    "AGENTBOX_ENDPOINT_STATE_KEYS",
    base64.urlsafe_b64encode(b"embedded-agentbox-endpoint-state-key").decode(),
)

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
local_app_module = importlib.import_module("local_app")


def test_local_app_embeds_agentbox_in_backend_process() -> None:
    app = local_app_module.create_local_app()

    assert app.state.embedded_worker is True
    assert app.state.embedded_agentbox is True
    assert app.state.embedded_apps == (local_app_module.AGENTBOX_MOUNT_PATH,)

    mount = next(
        route
        for route in app.routes
        if isinstance(route, Mount)
        and route.path == local_app_module.AGENTBOX_MOUNT_PATH
    )
    assert mount.app.state.shutdown_process_telemetry is False


def test_workspace_app_hosts_dispatch_to_embedded_agentbox(monkeypatch) -> None:
    from agentbox.api import apps as agentbox_apps

    monkeypatch.setattr(
        agentbox_apps.settings,
        "agentbox_app_domain",
        "workspaces.127-0-0-1.sslip.io:8711",
    )

    parent = FastAPI()
    manager = FastAPI()

    @parent.get("/{path:path}")
    async def parent_route(path: str) -> dict[str, str]:
        return {"app": "lemma", "path": path}

    @manager.get("/{path:path}")
    async def manager_route(path: str) -> dict[str, str]:
        return {"app": "agentbox", "path": path}

    routed = local_app_module.AgentBoxHostRoutingMiddleware(
        parent,
        agentbox_app=manager,
    )
    client = TestClient(routed)

    workspace = client.get(
        "/dashboard",
        headers={
            "host": "sandbox-1-browser.workspaces.127-0-0-1.sslip.io:8711"
        },
    )
    built_app = client.get(
        "/dashboard",
        headers={"host": "sales.apps.127-0-0-1.sslip.io:8711"},
    )

    assert workspace.json() == {"app": "agentbox", "path": "dashboard"}
    assert built_app.json() == {"app": "lemma", "path": "dashboard"}
