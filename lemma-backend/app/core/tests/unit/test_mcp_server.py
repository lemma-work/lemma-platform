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
    assert token.subject == "conversation-mcp"


@pytest.mark.asyncio
async def test_request_context_requires_conversation_and_bearer_headers(monkeypatch):
    conversation_id = uuid4()
    monkeypatch.setattr(
        mcp_server,
        "get_http_headers",
        lambda **_: {"x-lemma-conversation-id": str(conversation_id)},
    )
    with pytest.raises(ValueError, match="bearer token"):
        await mcp_server._request_context()

    monkeypatch.setattr(
        mcp_server,
        "get_http_headers",
        lambda **_: {
            "x-lemma-conversation-id": str(conversation_id),
            "authorization": "Bearer token",
            "x-lemma-agent-run-id": str(uuid4()),
        },
    )
    actual_conversation, token, run_id = await mcp_server._request_context()
    assert actual_conversation == conversation_id
    assert token == "token"
    assert run_id is not None


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
async def test_conversation_app_rejects_unknown_and_non_http_scopes():
    app = object.__new__(mcp_server.ConversationMCPASGIApp)
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
async def test_conversation_app_rewrites_conversation_route_for_fastmcp():
    captured: list[dict] = []
    app = object.__new__(mcp_server.ConversationMCPASGIApp)

    async def fake_mcp(scope, receive, send):
        captured.append(scope)

    app._mcp_app = fake_mcp
    conversation_id = uuid4()
    await app(
        _scope(f"/agent-runtime/conversations/{conversation_id}/mcp"),
        lambda: None,
        lambda _: None,
    )
    assert captured[0]["path"] == "/mcp"
    assert (b"x-lemma-conversation-id", str(conversation_id).encode()) in captured[0][
        "headers"
    ]


@pytest.mark.asyncio
async def test_parked_interaction_returns_pending_and_completed_responses(monkeypatch):
    conversation_id = uuid4()
    match = mcp_server._CONVERSATION_INTERACTION_PATH.match(
        f"/{conversation_id}/interactions/tool-1"
    )
    assert match is not None
    monkeypatch.setattr(
        mcp_server.conversation_mcp_service,
        "authorize",
        lambda **_: _authorized(),
    )

    async def pending(**_):
        return None

    async def completed(**_):
        return {"answer": "yes"}

    async def _run(parked):
        monkeypatch.setattr(
            mcp_server.conversation_mcp_service,
            "parked_tool_return",
            parked,
        )
        messages = await _capture_response(
            lambda send: mcp_server._serve_parked_interaction(
                match,
                _scope(
                    match.string,
                    headers=[(b"authorization", b"Bearer token")],
                ),
                lambda: None,
                send,
            )
        )
        return messages

    pending_messages = await _run(pending)
    assert pending_messages[0]["status"] == 204
    complete_messages = await _run(completed)
    assert complete_messages[0]["status"] == 200


async def _authorized() -> bool:
    return True


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
