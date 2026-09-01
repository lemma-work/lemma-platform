"""What an MCP server has to say for us to offer a sign-in button.

These run against an in-process ASGI app rather than mocks, because the parts
that broke in practice were protocol details a mock would have been written to
agree with: which well-known path answers, whether the challenge header is
parsed, whether a registration without a secret is accepted.
"""

from __future__ import annotations

import httpx
import pytest

from app.modules.connectors.services.auth.mcp_oauth import (
    McpAuthorizationUnavailable,
    discover_authorization_server,
    register_client,
)

pytestmark = pytest.mark.asyncio

_ORIGIN = "https://server.example"
_MCP_URL = f"{_ORIGIN}/mcp"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )


def _as_metadata(**overrides) -> dict:
    return {
        "issuer": _ORIGIN,
        "authorization_endpoint": f"{_ORIGIN}/oauth2/authorize",
        "token_endpoint": f"{_ORIGIN}/oauth2/token",
        "registration_endpoint": f"{_ORIGIN}/oauth2/register",
        "scopes_supported": ["read"],
        **overrides,
    }


def _server(
    *,
    challenge: str | None = None,
    resource_paths: tuple[str, ...] = ("/.well-known/oauth-protected-resource",),
    as_path: str = "/.well-known/oauth-authorization-server",
    as_metadata: dict | None = None,
):
    """A server answering only on the paths it is told to."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/mcp":
            headers = {"www-authenticate": challenge} if challenge else {}
            return httpx.Response(401, headers=headers, json={})
        if request.url.path in resource_paths:
            return httpx.Response(
                200,
                json={"resource": _MCP_URL, "authorization_servers": [_ORIGIN]},
            )
        if request.url.path == as_path:
            return httpx.Response(200, json=as_metadata or _as_metadata())
        return httpx.Response(404, json={})

    return handler, seen


async def test_the_challenge_header_points_at_the_metadata():
    """The header is authoritative, and following it means a server can serve
    its document from anywhere -- which is the point of announcing it."""
    handler, seen = _server(
        challenge=f'Bearer realm="x", resource_metadata="{_ORIGIN}/somewhere/else"',
        resource_paths=("/somewhere/else",),
    )
    async with _client(handler) as client:
        server = await discover_authorization_server(_MCP_URL, client=client)

    assert server is not None
    assert server.token_endpoint == f"{_ORIGIN}/oauth2/token"
    assert "/somewhere/else" in seen


async def test_the_path_suffixed_well_known_is_tried_before_the_bare_one():
    """A host serving several resources distinguishes them by path suffix, and
    a real server (Phoenix) answers only there. Trying the bare path first
    finds a different resource's document, or nothing."""
    handler, seen = _server(
        resource_paths=("/.well-known/oauth-protected-resource/mcp",)
    )
    async with _client(handler) as client:
        server = await discover_authorization_server(_MCP_URL, client=client)

    assert server is not None
    assert seen.index("/.well-known/oauth-protected-resource/mcp") < len(seen)
    assert "/.well-known/oauth-protected-resource" not in seen


async def test_openid_configuration_is_accepted_when_rfc8414_is_absent():
    """RFC 8414 inserts the well-known segment, OpenID appends it. Servers do
    both, and a client that knows only one finds nothing on the other."""
    handler, _ = _server(as_path="/.well-known/openid-configuration")
    async with _client(handler) as client:
        server = await discover_authorization_server(_MCP_URL, client=client)

    assert server is not None
    assert server.authorization_endpoint == f"{_ORIGIN}/oauth2/authorize"


async def test_a_server_wanting_no_authorization_is_not_an_error():
    """Answering 200 to an unauthenticated call means no sign-in is needed. A
    raise here would fail installs that work perfectly well."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    async with _client(handler) as client:
        assert await discover_authorization_server(_MCP_URL, client=client) is None


async def test_a_401_describing_nothing_we_can_follow_is_not_an_error():
    """A server can want a token without speaking the discovery RFCs. Pasting
    one in is still a valid way to reach it, so this must stay a None."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp":
            return httpx.Response(401, json={})
        return httpx.Response(404, json={})

    async with _client(handler) as client:
        assert await discover_authorization_server(_MCP_URL, client=client) is None


async def test_metadata_without_endpoints_is_rejected_rather_than_half_used():
    """An authorization server with no token endpoint cannot complete a flow.
    Accepting it would offer a sign-in button that always fails."""
    handler, _ = _server(
        as_metadata=_as_metadata(token_endpoint=None, registration_endpoint=None)
    )
    async with _client(handler) as client:
        assert await discover_authorization_server(_MCP_URL, client=client) is None


async def test_the_resource_comes_from_the_metadata_not_the_url_we_probed():
    """RFC 8707's indicator has to be the identifier the authorization server
    knows, which the document states. Assuming it equals the URL we happened to
    be given is how a token gets minted for a resource that will refuse it."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp":
            return httpx.Response(401, json={})
        if request.url.path == "/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(
                200,
                json={
                    "resource": f"{_ORIGIN}/canonical",
                    "authorization_servers": [_ORIGIN],
                },
            )
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=_as_metadata())
        return httpx.Response(404, json={})

    async with _client(handler) as client:
        server = await discover_authorization_server(_MCP_URL, client=client)

    assert server is not None
    assert server.resource == f"{_ORIGIN}/canonical"


async def test_registration_returning_no_secret_yields_a_public_client():
    """The common case. A secret of None is what tells the exchange to send no
    client authentication, matching the `none` we ask for here."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content))
        return httpx.Response(201, json={"client_id": "generated-id"})

    handler_disc, _ = _server()
    async with _client(handler_disc) as client:
        server = await discover_authorization_server(_MCP_URL, client=client)
    assert server is not None

    async with _client(handler) as client:
        client_id, client_secret = await register_client(
            server, redirect_uri="https://lemma.example/callback", client=client
        )

    assert (client_id, client_secret) == ("generated-id", None)
    assert captured["token_endpoint_auth_method"] == "none"
    assert captured["redirect_uris"] == ["https://lemma.example/callback"]


async def test_a_server_that_does_not_register_says_so_usefully():
    """No registration endpoint means no client can exist, and the person needs
    to be told to paste a token instead of watching a button do nothing."""
    handler, _ = _server(as_metadata=_as_metadata(registration_endpoint=None))
    async with _client(handler) as client:
        server = await discover_authorization_server(_MCP_URL, client=client)
    assert server is not None

    with pytest.raises(McpAuthorizationUnavailable, match="token instead"):
        await register_client(server, redirect_uri="https://lemma.example/callback")


async def test_a_refused_registration_is_reported_not_returned_empty():
    """A 400 with no client_id must not become an install that looks OAuth-ready
    and fails at the authorize step."""
    handler_disc, _ = _server()
    async with _client(handler_disc) as client:
        server = await discover_authorization_server(_MCP_URL, client=client)
    assert server is not None

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_redirect_uri"})

    async with _client(refuse) as client:
        with pytest.raises(McpAuthorizationUnavailable, match="HTTP 400"):
            await register_client(
                server, redirect_uri="https://lemma.example/callback", client=client
            )
