from __future__ import annotations

from uuid import uuid4

import pytest

import app.mcp_server as mcp_server

pytestmark = pytest.mark.unit


async def _capture_response(call):
    messages: list[dict] = []

    async def send(message):
        messages.append(message)

    await call(send)
    return messages


def _scope(path: str, *, headers=None, kind: str = "http") -> dict:
    return {
        "type": kind,
        "path": path,
        "raw_path": path.encode(),
        "headers": headers or [],
        "method": "GET",
    }


@pytest.mark.asyncio
async def test_auth_provider_accepts_nonempty_tokens_only():
    provider = mcp_server.LemmaMCPAuthProvider()
    assert await provider.verify_token("") is None
    token = await provider.verify_token("secret")
    assert token is not None
    assert token.token == "secret"
    assert token.subject == "pod-mcp"


@pytest.mark.asyncio
async def test_pod_request_context_requires_pod_and_bearer_headers(monkeypatch):
    pod_id = uuid4()
    monkeypatch.setattr(
        mcp_server,
        "get_http_headers",
        lambda **_: {
            "x-lemma-pod-id": str(pod_id),
            "authorization": "Bearer pod-token",
        },
    )
    actual_pod, token = await mcp_server._pod_request_context()
    assert actual_pod == pod_id
    assert token == "pod-token"


@pytest.mark.asyncio
async def test_pod_app_rejects_unknown_and_non_http_scopes():
    app = object.__new__(mcp_server.PodMCPASGIApp)
    app._mcp_app = None

    not_found = await _capture_response(
        lambda send: app(_scope("/not-mcp"), lambda: None, send)
    )
    assert not_found[0]["status"] == 404

    non_http = await _capture_response(
        lambda send: app(_scope("/ignored", kind="websocket"), lambda: None, send)
    )
    assert non_http[0]["status"] == 404


@pytest.mark.asyncio
async def test_pod_app_rewrites_pod_route_for_fastmcp():
    captured: list[dict] = []
    app = object.__new__(mcp_server.PodMCPASGIApp)

    async def fake_mcp(scope, receive, send):
        captured.append(scope)

    app._mcp_app = fake_mcp
    pod_id = uuid4()
    await app(
        _scope(f"/agent-runtime/pods/{pod_id}/mcp"),
        lambda: None,
        lambda _: None,
    )
    assert captured[0]["path"] == "/mcp"
    assert (b"x-lemma-pod-id", str(pod_id).encode()) in captured[0]["headers"]
