"""The `/workspace/*` routes machines replaced say where they went."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.api.exception_handlers import register_exception_handlers
from app.modules.workspace.api.controllers import port_proxy_controller
from app.modules.workspace.api.controllers.retired_workspace_routes import (
    RETIRED_BROWSER_VIEW,
    RETIRED_CODE,
    RETIRED_ROUTES,
    RETIRED_SOCKET_CLOSE_CODE,
    router,
)

pytestmark = pytest.mark.unit

#: Main's `/workspace/*` routes, from its OpenAPI spec, plus its socket.
MAINS_WORKSPACE_ROUTES = {
    ("GET", "/workspace/browser/current-page-url"),
    ("GET", "/workspace/browser/status"),
    ("GET", "/workspace/files"),
    ("GET", "/workspace/files:content"),
    ("GET", "/workspace/files:stat"),
    ("GET", "/workspace/status"),
    ("POST", "/workspace/apps/browser/access"),
    ("POST", "/workspace/browser/display-size"),
}


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    return TestClient(app)


def test_every_route_main_had_is_answered() -> None:
    assert {(r.method, r.path) for r in RETIRED_ROUTES} == MAINS_WORKSPACE_ROUTES


@pytest.mark.parametrize("retired", RETIRED_ROUTES, ids=lambda r: r.path)
def test_each_answers_gone_and_names_its_replacement(client, retired) -> None:
    response = client.request(retired.method, retired.path)

    assert response.status_code == 410
    body = response.json()
    assert body["code"] == RETIRED_CODE
    assert body["details"] == {"replacement": retired.replacement}
    assert retired.replacement in body["message"]
    assert "/machines/me" in retired.replacement


def test_the_browser_view_socket_closes_with_its_replacement(client) -> None:
    with client.websocket_connect(RETIRED_BROWSER_VIEW.path) as socket:
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_text()

    assert closed.value.code == RETIRED_SOCKET_CLOSE_CODE
    assert "/machines/me/browser/view" in closed.value.reason


def test_none_of_them_is_in_the_public_spec(client) -> None:
    paths = client.get("/openapi.json").json().get("paths", {})

    assert not [path for path in paths if path.startswith("/workspace/")]


def test_mains_port_grant_paths_are_still_served() -> None:
    """Main's grants point at `/public/workspace-ports` (and before that
    `/workspace-ports`); both live until the date in `LEGACY_ALIASES`."""
    paths = {
        getattr(route, "path", "") for route in port_proxy_controller.router.routes
    }

    for prefix in (
        "/public/machine-ports",
        "/public/workspace-ports",
        "/workspace-ports",
    ):
        assert f"{prefix}/{{token}}" in paths
        assert f"{prefix}/{{token}}/{{path:path}}" in paths
