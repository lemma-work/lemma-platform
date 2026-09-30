"""An outside MCP client, end to end: discover, register, consent, call tools.

Driven the way Claude and ChatGPT drive it -- every step over HTTP, the MCP
requests on the public ``/mcp/{pod_id}`` mount -- so a green run means a client
that follows the MCP authorization spec can connect a pod and use it, and that
each way a person takes access back actually takes it back.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.e2e

REDIRECT = "http://127.0.0.1:53682/callback"
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-06-18",
}


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return verifier, challenge


class _Client:
    """An MCP client with no Lemma session -- only what OAuth gives it."""

    def __init__(self, http: AsyncClient) -> None:
        self.http = http
        self.client_id = ""

    async def discover(self, pod_id: str) -> tuple[str, dict]:
        unauthenticated = await self.http.post(
            f"/mcp/{pod_id}",
            headers=MCP_HEADERS,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert unauthenticated.status_code == 401
        challenge = unauthenticated.headers["www-authenticate"]
        metadata_url = challenge.split('resource_metadata="', 1)[1].split('"', 1)[0]
        resource_metadata = (await self.http.get(urlsplit(metadata_url).path)).json()
        issuer = resource_metadata["authorization_servers"][0]
        assert (await self.http.get("/.well-known/oauth-authorization-server")).json()[
            "issuer"
        ] == issuer
        return resource_metadata["resource"], resource_metadata

    async def register(self) -> None:
        response = await self.http.post(
            "/oauth/register",
            json={
                "client_name": "E2E Client",
                "redirect_uris": [REDIRECT],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )
        assert response.status_code == 201, response.text
        self.client_id = response.json()["client_id"]

    async def authorize(self, resource: str, scope: str) -> tuple[str, str]:
        verifier, challenge = _pkce()
        response = await self.http.get(
            "/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": REDIRECT,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "the-state",
                "scope": scope,
                "resource": resource,
            },
        )
        assert response.status_code == 302, response.text
        consent_page = urlsplit(response.headers["location"])
        assert consent_page.path == "/auth/authorize"
        return parse_qs(consent_page.query)["request"][0], verifier

    async def redeem(self, code: str, verifier: str, resource: str) -> dict:
        response = await self.http.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT,
                "client_id": self.client_id,
                "code_verifier": verifier,
                "resource": resource,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    async def refresh(self, refresh_token: str):
        return await self.http.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.client_id,
            },
        )

    async def rpc(self, pod_id: str, token: str, method: str, params=None):
        return await self.http.post(
            f"/mcp/{pod_id}",
            headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"},
            json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}},
        )


async def _connect(client: _Client, person: AsyncClient, pod_id: str, scope: str):
    resource, _ = await client.discover(pod_id)
    request_id, verifier = await client.authorize(resource, scope)

    shown = await person.get(f"/oauth/consent/{request_id}")
    assert shown.status_code == 200, shown.text
    assert shown.json()["client_name"] == "E2E Client"
    assert shown.json()["redirect_host"] == "127.0.0.1:53682"

    answered = await person.post(f"/oauth/consent/{request_id}", json={"allow": True})
    assert answered.status_code == 200, answered.text
    back = urlsplit(answered.json()["redirect_to"])
    query = parse_qs(back.query)
    assert f"{back.scheme}://{back.netloc}{back.path}" == REDIRECT
    assert query["state"] == ["the-state"]
    assert query["iss"]  # RFC 9207
    return await client.redeem(query["code"][0], verifier, resource)


@asynccontextmanager
async def _pod_mcp_running(app):
    """Module e2e never runs the app's lifespan, and FastMCP's session manager
    exists only inside its own. Entered and left in one task, because the
    manager's task group refuses to be closed from another -- and a fixture's
    setup and teardown run in different ones."""
    ready, stop = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        async with app.state.pod_mcp_app.lifespan(app):
            ready.set()
            await stop.wait()

    holder = asyncio.create_task(hold())
    await ready.wait()
    try:
        yield
    finally:
        stop.set()
        await holder


@pytest.fixture
async def mcp_client(test_app):
    async with _pod_mcp_running(test_app):
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://testserver"
        ) as http:
            yield _Client(http)


async def test_a_client_connects_one_pod_and_works_as_the_person(
    mcp_client, authenticated_client, test_pod, other_pod
):
    pod_id = test_pod["id"]
    await mcp_client.register()
    tokens = await _connect(
        mcp_client, authenticated_client, pod_id, "pod:read pod:write"
    )
    assert tokens["scope"] == "pod:read pod:write"
    access = tokens["access_token"]

    listed = await mcp_client.rpc(pod_id, access, "tools/list")
    assert listed.status_code == 200, listed.text
    tools = {tool["name"]: tool for tool in listed.json()["result"]["tools"]}
    assert "lemma_pod_write_record" in tools
    assert tools["lemma_pod_tables"]["annotations"]["readOnlyHint"] is True
    assert tools["lemma_pod_write_record"]["annotations"]["destructiveHint"] is True

    called = await mcp_client.rpc(
        pod_id, access, "tools/call", {"name": "lemma_pod_tables", "arguments": {}}
    )
    assert called.status_code == 200, called.text
    result = called.json()["result"]
    assert result.get("isError") is not True, result
    assert result["structuredContent"]["success"] is True

    # The audience is the pod: the same token is refused by its sibling.
    elsewhere = await mcp_client.rpc(other_pod["id"], access, "tools/list")
    assert elsewhere.status_code == 401

    connected = await authenticated_client.get(
        "/oauth/grants", params={"pod_id": pod_id}
    )
    assert [item["client_name"] for item in connected.json()["items"]] == ["E2E Client"]


async def test_refresh_rotates_and_a_replayed_refresh_token_ends_the_grant(
    mcp_client, authenticated_client, test_pod
):
    pod_id = test_pod["id"]
    await mcp_client.register()
    first = await _connect(mcp_client, authenticated_client, pod_id, "pod:read")

    rotated = await mcp_client.refresh(first["refresh_token"])
    assert rotated.status_code == 200, rotated.text
    second = rotated.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert (
        await mcp_client.rpc(pod_id, second["access_token"], "tools/list")
    ).status_code == 200

    # The old refresh token again: two parties hold it, so nobody keeps it.
    replayed = await mcp_client.refresh(first["refresh_token"])
    assert replayed.status_code in (400, 401)
    assert replayed.json()["error"] == "invalid_grant"
    after = await mcp_client.rpc(pod_id, second["access_token"], "tools/list")
    assert after.status_code == 401


async def test_revoking_a_connection_ends_its_tokens_at_once(
    mcp_client, authenticated_client, test_pod
):
    pod_id = test_pod["id"]
    await mcp_client.register()
    tokens = await _connect(mcp_client, authenticated_client, pod_id, "pod:read")
    assert (
        await mcp_client.rpc(pod_id, tokens["access_token"], "tools/list")
    ).status_code == 200

    grant_id = (
        await authenticated_client.get("/oauth/grants", params={"pod_id": pod_id})
    ).json()["items"][0]["grant_id"]
    revoked = await authenticated_client.delete(f"/oauth/grants/{grant_id}")
    assert revoked.status_code == 204

    assert (
        await mcp_client.rpc(pod_id, tokens["access_token"], "tools/list")
    ).status_code == 401
    refreshed = await mcp_client.refresh(tokens["refresh_token"])
    assert refreshed.json()["error"] == "invalid_grant"


async def test_a_read_only_connection_is_not_offered_the_writing_tools(
    mcp_client, authenticated_client, test_pod
):
    pod_id = test_pod["id"]
    await mcp_client.register()
    tokens = await _connect(mcp_client, authenticated_client, pod_id, "pod:read")
    listed = await mcp_client.rpc(pod_id, tokens["access_token"], "tools/list")
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "lemma_pod_tables" in names
    assert not names & {
        "lemma_pod_write_record",
        "lemma_pod_write_file",
        "lemma_pod_edit_file",
    }

    refused = await mcp_client.rpc(
        pod_id,
        tokens["access_token"],
        "tools/call",
        {
            "name": "lemma_pod_write_record",
            "arguments": {"request": {"action": "create", "table_name": "x"}},
        },
    )
    assert refused.json()["result"]["isError"] is True


async def test_a_denied_consent_redirects_with_access_denied(
    mcp_client, authenticated_client, test_pod
):
    await mcp_client.register()
    resource, _ = await mcp_client.discover(test_pod["id"])
    request_id, _ = await mcp_client.authorize(resource, "pod:read")
    answered = await authenticated_client.post(
        f"/oauth/consent/{request_id}", json={"allow": False}
    )
    query = parse_qs(urlsplit(answered.json()["redirect_to"]).query)
    assert query["error"] == ["access_denied"]
    assert "code" not in query
    # One answer per request.
    again = await authenticated_client.post(
        f"/oauth/consent/{request_id}", json={"allow": True}
    )
    assert again.status_code == 404


async def test_a_person_cannot_hand_over_a_pod_they_are_not_in(
    mcp_client, authenticated_client, test_pod, async_client_for_stranger
):
    await mcp_client.register()
    resource, _ = await mcp_client.discover(test_pod["id"])
    request_id, _ = await mcp_client.authorize(resource, "pod:read")
    shown = await async_client_for_stranger.get(f"/oauth/consent/{request_id}")
    assert shown.status_code == 403
    answered = await async_client_for_stranger.post(
        f"/oauth/consent/{request_id}", json={"allow": True}
    )
    assert answered.status_code == 403
