"""Private apps in a real browser: direct visits, workspace tabs, sign-in and failures."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import re
import socket
from uuid import uuid4
from zipfile import ZipFile

from fastapi.responses import HTMLResponse
import pytest
from starlette.middleware.cors import CORSMiddleware
import uvicorn

from app.core.config import settings
from app.modules.apps.tests.e2e.test_app_e2e import build_dist_archive
from app.modules.test_support.e2e.waiters import eventually

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "direct",
        "workspace",
        "signed-out",
        "workspace-signed-out",
        "login-return",
        "html-login-return",
        "nested-html-login-return",
        "concurrent",
        "blocked-cookies",
        "service-down",
    ],
)
async def test_private_app_browser_direct_and_workspace(
    test_app,
    authenticated_client,
    test_pod,
    fixed_test_user,
    monkeypatch,
    tmp_path,
    mode,
):
    workspace = mode.startswith("workspace")
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    process = await asyncio.create_subprocess_exec(
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-subj",
        "/CN=example.test",
        "-keyout",
        str(key),
        "-out",
        str(cert),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert await process.wait() == 0
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    api_origin = f"https://api.example.test:{port}"
    workspace_origin = f"https://workspace.example.test:{port}"
    monkeypatch.setattr(settings, "api_url", f"{api_origin}")
    monkeypatch.setattr(settings, "app_base_domain", f"apps.example.test:{port}")
    monkeypatch.setattr(settings, "frontend_url", f"{workspace_origin}")
    monkeypatch.setattr(
        settings,
        "auth_frontend_url",
        str(workspace_origin),
    )
    monkeypatch.setattr(settings, "app_api_via_app_origin", False)
    middleware = test_app.middleware_stack
    while middleware is not None:
        if isinstance(middleware, CORSMiddleware):
            monkeypatch.setattr(middleware, "allow_origins", [workspace_origin])
            monkeypatch.setattr(
                middleware,
                "allow_origin_regex",
                re.compile(rf"https://[a-z0-9-]+\.apps\.example\.test:{port}"),
            )
        middleware = getattr(middleware, "app", None)
    slug = f"browser-{uuid4().hex[:8]}"
    origin = f"https://{slug}.apps.example.test:{port}"
    created = await authenticated_client.post(
        f"/pods/{test_pod['id']}/apps",
        json={"name": slug, "public_slug": slug, "visibility": "POD"},
    )
    assert created.status_code == 201, created.text
    archive = io.BytesIO(build_dist_archive("PRIVATE_APP_CONTENT"))
    with ZipFile(archive, "a") as bundle:
        for path in ["reports.html", "nested/index.html"]:
            bundle.writestr(path, "<html><body>PRIVATE_APP_CONTENT</body></html>")
    uploaded = await authenticated_client.post(
        f"/pods/{test_pod['id']}/apps/{slug}/bundle",
        files={
            "dist_archive": (
                "dist.zip",
                archive.getvalue(),
                "application/zip",
            )
        },
    )
    assert uploaded.status_code == 200, uploaded.text

    async def workspace_page():
        # The workspace frames the app's address and does nothing else: the
        # framed sign-in page asks the API for its own ticket.
        return HTMLResponse(f'<iframe src="{origin}"></iframe>')

    route_count = len(test_app.router.routes)
    test_app.add_api_route("/public/sdk/private-app-test-workspace", workspace_page)

    async def browser_hosts(scope, receive, send):
        # The workspace portal is a separate service, outside the API's global
        # authentication middleware. Keep its real /auth URL in this fixture.
        if (
            scope["type"] == "http"
            and scope["path"] == "/auth"
            and (b"host", f"workspace.example.test:{port}".encode()) in scope["headers"]
        ):
            await HTMLResponse("<h1>Sign in</h1>")(scope, receive, send)
            return
        await test_app(scope, receive, send)

    server = uvicorn.Server(
        uvicorn.Config(
            browser_hosts,
            lifespan="off",
            ssl_certfile=str(cert),
            ssl_keyfile=str(key),
            access_log=False,
            log_level="error",
        )
    )
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:

        async def server_ready():
            if serving.done():
                await serving
                pytest.fail("Browser test server did not start")
            return server.started

        await eventually(
            label="hosted app TLS server",
            probe=server_ready,
            done=bool,
            timeout_seconds=10,
        )
        runner = (
            Path(__file__).resolve().parents[6]
            / "lemma-frontend/tests/browser/private-app-runner.mjs"
        )
        browser = await asyncio.create_subprocess_exec(
            "node",
            str(runner),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        token = authenticated_client.headers["authorization"].removeprefix("Bearer ")
        payload = json.dumps(
            {
                "origin": origin,
                "token": token,
                "email": fixed_test_user["email"],
                "apiOrigin": api_origin,
                "workspaceOrigin": workspace_origin,
                "mode": mode,
                "path": {
                    "html-login-return": "/reports.html?period=current#totals",
                    "nested-html-login-return": "/nested/index.html?period=current#totals",
                }.get(mode, "/deep/path?mode=study#section"),
                "workspace": workspace_origin + "/public/sdk/private-app-test-workspace"
                if workspace
                else None,
            }
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                browser.communicate(payload.encode()), timeout=40
            )
        finally:
            if browser.returncode is None:
                browser.kill()
                await browser.wait()
        assert browser.returncode == 0, stderr.decode()
        assert b"Private app browser access passed" in stdout
    finally:
        server.should_exit = True
        await serving
        listener.close()
        del test_app.router.routes[route_count:]
