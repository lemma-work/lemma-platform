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


@pytest.mark.asyncio
async def test_agent_host_mount_refuses_an_outside_clients_token():
    """Served there, it would skip the public mount's rate limit."""
    forwarded: list[dict] = []
    app = object.__new__(mcp_server.PodMCPASGIApp)

    async def fake_mcp(scope, receive, send):
        forwarded.append(scope)

    app._mcp_app = fake_mcp
    messages = await _capture_response(
        lambda send: app(
            _scope(
                f"/agent-runtime/pods/{uuid4()}/mcp",
                headers=[(b"authorization", b"Bearer lemma_mcp_at_x")],
            ),
            lambda: None,
            send,
        )
    )
    assert messages[0]["status"] == 401
    assert forwarded == []


# --- The public mount, /mcp/{pod_id} -----------------------------------------


def _principal(pod_id):
    from app.modules.mcp_access.contracts import McpPrincipal, Scope

    return McpPrincipal(
        user_id=uuid4(),
        pod_id=pod_id,
        grant_id=uuid4(),
        client_id="client",
        client_name="Claude",
        scopes=frozenset({Scope.READ}),
    )


async def _accept(token, *, pod_id):
    return _principal(pod_id)


async def _refuse(token, *, pod_id):
    return None


async def _no_session(*, pod_id, token):
    return False


async def _no_wait(principal):
    return None


def _public_app(
    forwarded: list[dict],
    *,
    verify=_accept,
    retry_after=_no_wait,
    origin_allowed=lambda origin: origin == "https://lemma.work",
):
    async def fake_mcp(scope, receive, send):
        forwarded.append(scope)

    pod_app = object.__new__(mcp_server.PodMCPASGIApp)
    pod_app._mcp_app = fake_mcp
    return mcp_server.PublicPodMCPApp(
        pod_app,
        mcp_server.PublicMCPGate(
            verify_access_token=verify,
            authorize_session=_no_session,
            retry_after=retry_after,
            origin_allowed=origin_allowed,
        ),
    )


_BEARER = (b"authorization", b"Bearer lemma_mcp_at_x")


@pytest.mark.asyncio
async def test_public_mount_challenges_a_request_without_a_token():
    """Claude starts its sign-in only on a 401 that names the metadata
    document -- a JSON-RPC error on a 200 would leave it stuck."""
    forwarded: list[dict] = []
    pod_id = uuid4()
    messages = await _capture_response(
        lambda send: _public_app(forwarded)(
            _scope(f"/mcp/{pod_id}"), lambda: None, send
        )
    )
    assert messages[0]["status"] == 401
    challenge = dict(messages[0]["headers"])[b"www-authenticate"].decode()
    assert challenge.startswith("Bearer ")
    assert f"oauth-protected-resource/mcp/{pod_id}" in challenge
    assert 'scope="pod:read pod:write"' in challenge
    assert forwarded == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bearer", [b"Bearer lemma_mcp_at_x", b"Bearer a-session"])
async def test_public_mount_calls_a_refused_token_invalid(bearer):
    forwarded: list[dict] = []
    messages = await _capture_response(
        lambda send: _public_app(forwarded, verify=_refuse)(
            _scope(f"/mcp/{uuid4()}", headers=[(b"authorization", bearer)]),
            lambda: None,
            send,
        )
    )
    assert messages[0]["status"] == 401
    challenge = dict(messages[0]["headers"])[b"www-authenticate"]
    assert b'error="invalid_token"' in challenge
    assert forwarded == []


@pytest.mark.asyncio
async def test_public_mount_forwards_a_good_token_for_its_own_pod_only():
    pod_id = uuid4()
    forwarded: list[dict] = []
    await _public_app(forwarded)(
        _scope(
            f"/mcp/{pod_id}",
            headers=[
                _BEARER,
                # A client naming another pod in the header the wrapper sets.
                (b"x-lemma-pod-id", str(uuid4()).encode()),
            ],
        ),
        lambda: None,
        lambda _: None,
    )
    pod_headers = [v for k, v in forwarded[0]["headers"] if k == b"x-lemma-pod-id"]
    assert pod_headers == [str(pod_id).encode()]
    assert forwarded[0]["path"] == "/mcp"
    assert forwarded[0]["root_path"] == ""


@pytest.mark.asyncio
async def test_public_mount_refuses_an_untrusted_browser_origin():
    forwarded: list[dict] = []
    messages = await _capture_response(
        lambda send: _public_app(forwarded)(
            _scope(
                f"/mcp/{uuid4()}",
                headers=[(b"origin", b"https://evil.example"), _BEARER],
            ),
            lambda: None,
            send,
        )
    )
    assert messages[0]["status"] == 403
    assert forwarded == []


@pytest.mark.asyncio
async def test_public_mount_answers_429_over_the_grants_budget():
    async def wait(principal):
        return 17

    forwarded: list[dict] = []
    messages = await _capture_response(
        lambda send: _public_app(forwarded, retry_after=wait)(
            _scope(f"/mcp/{uuid4()}", headers=[_BEARER]),
            lambda: None,
            send,
        )
    )
    assert messages[0]["status"] == 429
    assert dict(messages[0]["headers"])[b"retry-after"] == b"17"
    assert forwarded == []


# --- Two Authorization headers, either mount ---------------------------------

_TWO_BEARERS = [
    (b"authorization", b"Bearer a-session"),
    (b"authorization", b"Bearer lemma_mcp_at_x"),
]


@pytest.mark.asyncio
async def test_the_agent_host_mount_refuses_two_authorization_headers():
    """The door reads the first, the tools read the last: an outside client's
    token sent second would be served here without its rate limit."""
    forwarded: list[dict] = []
    app = object.__new__(mcp_server.PodMCPASGIApp)

    async def fake_mcp(scope, receive, send):
        forwarded.append(scope)

    app._mcp_app = fake_mcp
    messages = await _capture_response(
        lambda send: app(
            _scope(f"/agent-runtime/pods/{uuid4()}/mcp", headers=_TWO_BEARERS),
            lambda: None,
            send,
        )
    )
    assert messages[0]["status"] == 400
    assert forwarded == []


@pytest.mark.asyncio
async def test_the_public_mount_refuses_two_authorization_headers():
    forwarded: list[dict] = []

    async def session_ok(*, pod_id, token):
        return True

    app = _public_app(forwarded)
    app._gate = mcp_server.PublicMCPGate(
        verify_access_token=_accept,
        authorize_session=session_ok,
        retry_after=_no_wait,
        origin_allowed=lambda origin: True,
    )
    messages = await _capture_response(
        lambda send: app(
            _scope(f"/mcp/{uuid4()}", headers=_TWO_BEARERS), lambda: None, send
        )
    )
    assert messages[0]["status"] == 400
    assert forwarded == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/mcp/" + "-" * 36, "/agent-runtime/pods/" + "a" * 36 + "/mcp"]
)
async def test_a_pod_id_shaped_but_not_a_uuid_is_a_404(path):
    forwarded: list[dict] = []
    pod_app = object.__new__(mcp_server.PodMCPASGIApp)

    async def fake_mcp(scope, receive, send):
        forwarded.append(scope)

    pod_app._mcp_app = fake_mcp
    app = _public_app(forwarded) if path.startswith("/mcp/") else pod_app
    messages = await _capture_response(
        lambda send: app(_scope(path, headers=[_BEARER]), lambda: None, send)
    )
    assert messages[0]["status"] == 404
    assert forwarded == []
