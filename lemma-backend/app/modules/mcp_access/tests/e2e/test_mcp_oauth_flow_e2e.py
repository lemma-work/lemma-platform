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
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from sqlalchemy import text
from httpx import ASGITransport, AsyncClient

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.mcp_access.domain.entities import TokenKind
from app.modules.mcp_access.infrastructure.repositories import McpAccessRepository

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


async def _connect(
    client: _Client,
    person: AsyncClient,
    pod_id: str,
    scope: str,
    *,
    spell_resource=lambda resource: resource,
):
    resource, _ = await client.discover(pod_id)
    resource = spell_resource(resource)
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
    mcp_client, authenticated_client, test_pod, db_session
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

    # Straight after rotation the old token is the same client retrying a
    # response it lost, and gets a fresh pair rather than ending the grant.
    retried = await mcp_client.refresh(first["refresh_token"])
    assert retried.status_code == 200, retried.text
    third = retried.json()
    assert (
        await mcp_client.rpc(pod_id, third["access_token"], "tools/list")
    ).status_code == 200
    # The retry's pair replaces the first exchange's; the grant never holds
    # two usable pairs, so whoever has the losing one is refused.
    assert (
        await mcp_client.rpc(pod_id, second["access_token"], "tools/list")
    ).status_code == 401

    # Well after rotation, it is someone else holding a copy: nobody keeps it.
    await db_session.execute(
        text(
            "UPDATE mcp_oauth_tokens SET rotated_at = rotated_at - interval '5 minutes' "
            "WHERE rotated_at IS NOT NULL"
        )
    )
    await db_session.commit()
    replayed = await mcp_client.refresh(first["refresh_token"])
    assert replayed.status_code in (400, 401)
    assert replayed.json()["error"] == "invalid_grant"
    after = await mcp_client.rpc(pod_id, third["access_token"], "tools/list")
    assert after.status_code == 401


async def test_two_retries_at_once_take_turns_and_leave_one_live_refresh_token(
    mcp_client, authenticated_client, test_pod, db_manager
):
    """Driven at the repository, two sessions deep, because two HTTP requests
    in-process never overlap: the second retry waits for the first to commit,
    then cancels the pair the first one wrote."""
    pod_id = test_pod["id"]
    await mcp_client.register()
    first = await _connect(mcp_client, authenticated_client, pod_id, "pod:read")
    assert (await mcp_client.refresh(first["refresh_token"])).status_code == 200
    grant_id = UUID(
        (
            await authenticated_client.get("/oauth/grants", params={"pod_id": pod_id})
        ).json()["items"][0]["grant_id"]
    )
    now = datetime.now(timezone.utc)
    # With no access token for both retries to delete, nothing but the grant
    # lock orders them: row locks on the tokens alone would let each commit a
    # pair the other never saw.
    async with db_manager.session_factory() as setup:
        await setup.execute(
            text(
                "DELETE FROM mcp_oauth_tokens WHERE grant_id = :grant AND kind = 'access'"
            ),
            {"grant": grant_id},
        )
        await setup.commit()

    async def retry(repository: McpAccessRepository, token_hash: str) -> None:
        await repository.supersede_live_tokens(
            grant_id=grant_id, rotated_at=now, now=now
        )
        await repository.add_token(
            token_hash=token_hash,
            grant_id=grant_id,
            kind=TokenKind.REFRESH,
            scopes=["pod:read"],
            expires_at=now + timedelta(days=1),
        )

    async with (
        db_manager.session_factory() as one,
        db_manager.session_factory() as two,
    ):
        await retry(McpAccessRepository(SqlAlchemyUnitOfWork(one)), "a" * 64)
        second = asyncio.create_task(
            retry(McpAccessRepository(SqlAlchemyUnitOfWork(two)), "b" * 64)
        )
        await asyncio.sleep(0.5)
        assert not second.done(), "the second retry did not wait for the first"
        await one.commit()
        await second
        await two.commit()

    async with db_manager.session_factory() as check:
        live = (
            (
                await check.execute(
                    text(
                        "SELECT token_hash FROM mcp_oauth_tokens WHERE grant_id = :grant "
                        "AND kind = 'refresh' AND rotated_at IS NULL"
                    ),
                    {"grant": grant_id},
                )
            )
            .scalars()
            .all()
        )
    assert live == ["b" * 64]


async def test_the_superseded_refresh_token_is_a_replay_after_the_grace(
    mcp_client, authenticated_client, test_pod, db_session
):
    """The pair a retry cancelled cannot come back later: its refresh token,
    presented after the grace, ends the grant rather than being honoured."""
    pod_id = test_pod["id"]
    await mcp_client.register()
    first = await _connect(mcp_client, authenticated_client, pod_id, "pod:read")
    second = (await mcp_client.refresh(first["refresh_token"])).json()
    third = (await mcp_client.refresh(first["refresh_token"])).json()

    await db_session.execute(
        text(
            "UPDATE mcp_oauth_tokens SET rotated_at = rotated_at - interval '5 minutes' "
            "WHERE rotated_at IS NOT NULL"
        )
    )
    await db_session.commit()
    replayed = await mcp_client.refresh(second["refresh_token"])
    assert replayed.status_code in (400, 401)
    assert replayed.json()["error"] == "invalid_grant"
    after = await mcp_client.rpc(pod_id, third["access_token"], "tools/list")
    assert after.status_code == 401


async def test_a_token_retried_twice_ends_the_grant(
    mcp_client, authenticated_client, test_pod
):
    """One retry is a lost response. Two is somebody else."""
    pod_id = test_pod["id"]
    await mcp_client.register()
    first = await _connect(mcp_client, authenticated_client, pod_id, "pod:read")
    assert (await mcp_client.refresh(first["refresh_token"])).status_code == 200
    retried = await mcp_client.refresh(first["refresh_token"])
    assert retried.status_code == 200, retried.text

    again = await mcp_client.refresh(first["refresh_token"])
    assert again.status_code in (400, 401)
    assert again.json()["error"] == "invalid_grant"
    after = await mcp_client.rpc(pod_id, retried.json()["access_token"], "tools/list")
    assert after.status_code == 401


async def test_the_cancelled_token_still_ends_the_grant_after_the_winner_rotates(
    mcp_client, authenticated_client, test_pod, db_session
):
    """The party a retry cancelled keeps a token that later rotations must not
    prune away: presented afterwards, it is caught, not merely unknown."""
    pod_id = test_pod["id"]
    await mcp_client.register()
    first = await _connect(mcp_client, authenticated_client, pod_id, "pod:read")
    second = (await mcp_client.refresh(first["refresh_token"])).json()
    third = (await mcp_client.refresh(first["refresh_token"])).json()
    fourth = await mcp_client.refresh(third["refresh_token"])
    assert fourth.status_code == 200, fourth.text

    await db_session.execute(
        text(
            "UPDATE mcp_oauth_tokens SET rotated_at = rotated_at - interval '5 minutes' "
            "WHERE rotated_at IS NOT NULL"
        )
    )
    await db_session.commit()
    replayed = await mcp_client.refresh(second["refresh_token"])
    assert replayed.json()["error"] == "invalid_grant"
    after = await mcp_client.rpc(pod_id, fourth.json()["access_token"], "tools/list")
    assert after.status_code == 401


async def test_a_deleted_pod_refuses_its_clients_and_ends_their_grants(
    mcp_client, authenticated_client, test_pod, db_manager
):
    pod_id = test_pod["id"]
    await mcp_client.register()
    tokens = await _connect(mcp_client, authenticated_client, pod_id, "pod:read")
    assert (await authenticated_client.delete(f"/pods/{pod_id}")).status_code == 204

    # Refused at once, before anything reacts to the deletion event.
    served = await mcp_client.rpc(pod_id, tokens["access_token"], "tools/list")
    assert served.status_code == 401
    refreshed = await mcp_client.refresh(tokens["refresh_token"])
    assert refreshed.json()["error"] == "invalid_grant"

    # And the event's handler ends the grants themselves.
    async with db_manager.session_factory() as session:
        ended = await McpAccessRepository(
            SqlAlchemyUnitOfWork(session)
        ).revoke_pod_grants(pod_id=UUID(pod_id), now=datetime.now(timezone.utc))
        await session.commit()
    assert ended == 1


async def test_a_public_client_disconnects_with_only_its_token(
    mcp_client, authenticated_client, test_pod
):
    """Claude and Claude Code have no secret to send; revoking still works,
    and ends the connection rather than one token."""
    pod_id = test_pod["id"]
    await mcp_client.register()
    tokens = await _connect(mcp_client, authenticated_client, pod_id, "pod:read")

    revoked = await mcp_client.http.post(
        "/oauth/revoke",
        data={"token": tokens["refresh_token"], "client_id": mcp_client.client_id},
    )
    assert revoked.status_code == 200, revoked.text
    after = await mcp_client.rpc(pod_id, tokens["access_token"], "tools/list")
    assert after.status_code == 401
    listed = await authenticated_client.get("/oauth/grants", params={"pod_id": pod_id})
    assert listed.json()["items"] == []


async def test_an_oversized_registration_is_refused_before_it_is_held(mcp_client):
    padded = await mcp_client.http.post(
        "/oauth/register",
        json={
            "client_name": "x" * 40_000,
            "redirect_uris": [REDIRECT],
            "token_endpoint_auth_method": "none",
        },
    )
    assert padded.status_code == 413
    long_name = await mcp_client.http.post(
        "/oauth/register",
        json={
            "client_name": "x" * 1_000,
            "redirect_uris": [REDIRECT],
            "token_endpoint_auth_method": "none",
        },
    )
    assert long_name.status_code == 400
    assert long_name.json()["error"] == "invalid_client_metadata"


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

    # An in-app link is offered; a public one is publishing, and refused.
    assert "lemma_pod_get_file_url" in names
    published = await mcp_client.rpc(
        pod_id,
        tokens["access_token"],
        "tools/call",
        {
            "name": "lemma_pod_get_file_url",
            "arguments": {"request": {"path": "/any.pdf", "url_type": "public"}},
        },
    )
    assert published.json()["result"]["isError"] is True
    assert "pod:write" in published.json()["result"]["content"][0]["text"]


async def test_a_host_finds_the_table_view_and_the_rows_it_draws(
    mcp_client, authenticated_client, test_pod
):
    """What an MCP Apps host does with a read-only connection: find the view a
    tool names, read it from this server, and hand it a result -- here, the
    page the view's own Next button asks for."""
    pod_id = test_pod["id"]
    created = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": "orders",
            "columns": [
                {"name": "customer", "type": "TEXT"},
                {"name": "amount", "type": "FLOAT"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    for customer, amount in (("Ada", 12.5), ("Grace", 40.0), ("Linus", 7.0)):
        added = await authenticated_client.post(
            f"/pods/{pod_id}/datastore/tables/orders/records",
            json={"data": {"customer": customer, "amount": amount}},
        )
        assert added.status_code == 201, added.text

    await mcp_client.register()
    tokens = await _connect(mcp_client, authenticated_client, pod_id, "pod:read")
    access = tokens["access_token"]

    listed = await mcp_client.rpc(pod_id, access, "tools/list")
    views = {
        tool["name"]: tool["_meta"].get("ui", {}).get("resourceUri")
        for tool in listed.json()["result"]["tools"]
    }
    assert views["lemma_pod_get_records"] == "ui://lemma/table"
    assert views["lemma_pod_query"] == "ui://lemma/table"
    assert views["lemma_pod_tables"] is None

    read = await mcp_client.rpc(
        pod_id, access, "resources/read", {"uri": "ui://lemma/table"}
    )
    assert read.status_code == 200, read.text
    [view] = read.json()["result"]["contents"]
    assert view["mimeType"] == "text/html;profile=mcp-app"
    assert view["_meta"]["ui"]["csp"] == {"connectDomains": [], "resourceDomains": []}
    assert "ui/initialize" in view["text"]

    page = await mcp_client.rpc(
        pod_id,
        access,
        "tools/call",
        {
            "name": "lemma_pod_get_records",
            "arguments": {
                "table_name": "orders",
                "offset": 2,
                "limit": 2,
                "sorts": [{"column": "amount", "direction": "desc"}],
            },
        },
    )
    records = page.json()["result"]["structuredContent"]
    assert records["total"] == 3
    assert [row["customer"] for row in records["records"]] == ["Linus"]

    query = await mcp_client.rpc(
        pod_id,
        access,
        "tools/call",
        {
            "name": "lemma_pod_query",
            "arguments": {"sql": "select customer from orders order by amount"},
        },
    )
    rows = query.json()["result"]["structuredContent"]
    assert [row["customer"] for row in rows["rows"]] == ["Linus", "Ada", "Grace"]
    assert rows["truncated"] is False


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


async def test_a_resource_spelled_differently_still_works_once_connected(
    mcp_client, authenticated_client, test_pod
):
    """RFC 3986 calls a trailing slash the same resource; authorize accepts it,
    so the token it leads to must work too, not 401 in a sign-in loop."""
    pod_id = test_pod["id"]
    await mcp_client.register()
    tokens = await _connect(
        mcp_client,
        authenticated_client,
        pod_id,
        "pod:read",
        spell_resource=lambda resource: resource + "/",
    )
    listed = await mcp_client.rpc(pod_id, tokens["access_token"], "tools/list")
    assert listed.status_code == 200, listed.text


async def test_each_consent_is_its_own_connection(
    mcp_client, authenticated_client, test_pod
):
    """The same app on two devices is two connections: listed apart, and
    disconnecting one leaves the other working."""
    pod_id = test_pod["id"]
    await mcp_client.register()
    laptop = await _connect(
        mcp_client, authenticated_client, pod_id, "pod:read pod:write"
    )
    desktop = await _connect(mcp_client, authenticated_client, pod_id, "pod:read")

    listed = (
        await authenticated_client.get("/oauth/grants", params={"pod_id": pod_id})
    ).json()["items"]
    assert len(listed) == 2

    desktop_tools = await mcp_client.rpc(pod_id, desktop["access_token"], "tools/list")
    names = {tool["name"] for tool in desktop_tools.json()["result"]["tools"]}
    assert "lemma_pod_write_record" not in names

    newest = listed[0]["grant_id"]
    assert (
        await authenticated_client.delete(f"/oauth/grants/{newest}")
    ).status_code == 204
    assert (
        await mcp_client.rpc(pod_id, desktop["access_token"], "tools/list")
    ).status_code == 401
    assert (
        await mcp_client.rpc(pod_id, laptop["access_token"], "tools/list")
    ).status_code == 200


async def test_the_person_can_allow_reading_only_whatever_the_app_asked_for(
    mcp_client, authenticated_client, test_pod
):
    pod_id = test_pod["id"]
    await mcp_client.register()
    resource, _ = await mcp_client.discover(pod_id)
    request_id, verifier = await mcp_client.authorize(resource, "pod:read pod:write")
    answered = await authenticated_client.post(
        f"/oauth/consent/{request_id}", json={"allow": True, "read_only": True}
    )
    code = parse_qs(urlsplit(answered.json()["redirect_to"]).query)["code"][0]
    tokens = await mcp_client.redeem(code, verifier, resource)
    assert tokens["scope"] == "pod:read"
    listed = await mcp_client.rpc(pod_id, tokens["access_token"], "tools/list")
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "lemma_pod_write_record" not in names


async def test_reading_only_on_a_write_only_request_grants_reading_not_everything(
    mcp_client, authenticated_client, test_pod
):
    """It used to store no scopes, and no scopes read back as all of them."""
    pod_id = test_pod["id"]
    await mcp_client.register()
    resource, _ = await mcp_client.discover(pod_id)
    request_id, verifier = await mcp_client.authorize(resource, "pod:write")
    answered = await authenticated_client.post(
        f"/oauth/consent/{request_id}", json={"allow": True, "read_only": True}
    )
    code = parse_qs(urlsplit(answered.json()["redirect_to"]).query)["code"][0]
    tokens = await mcp_client.redeem(code, verifier, resource)
    assert tokens["scope"] == "pod:read"
    listed = await authenticated_client.get("/oauth/grants", params={"pod_id": pod_id})
    assert listed.json()["items"][0]["scopes"] == ["pod:read"]


async def test_a_pods_admin_sees_everyones_connections_and_nobody_else_does(
    mcp_client,
    authenticated_client,
    test_pod,
    async_client_for_stranger,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    await mcp_client.register()
    await _connect(mcp_client, authenticated_client, pod_id, "pod:read")

    everyone = await authenticated_client.get(
        "/oauth/grants", params={"pod_id": pod_id, "everyone": True}
    )
    assert everyone.status_code == 200, everyone.text
    assert [item["user_id"] for item in everyone.json()["items"]] == [
        fixed_test_user["id"]
    ]
    refused = await async_client_for_stranger.get(
        "/oauth/grants", params={"pod_id": pod_id, "everyone": True}
    )
    assert refused.status_code == 403
    # Nor can an outsider end somebody else's connection.
    grant_id = everyone.json()["items"][0]["grant_id"]
    assert (
        await async_client_for_stranger.delete(f"/oauth/grants/{grant_id}")
    ).status_code == 404


async def test_a_registered_app_is_not_stored_until_someone_allows_it(
    mcp_client, authenticated_client, test_pod, db_session
):
    async def stored() -> int:
        return (
            await db_session.execute(text("SELECT count(*) FROM mcp_oauth_clients"))
        ).scalar_one()

    before = await stored()
    await mcp_client.register()
    assert await stored() == before
    await _connect(mcp_client, authenticated_client, test_pod["id"], "pod:read")
    assert await stored() == before + 1


async def test_registering_a_redirect_that_runs_code_is_refused(mcp_client):
    response = await mcp_client.http.post(
        "/oauth/register",
        json={
            "client_name": "Claude",
            "redirect_uris": ["javascript://evil.example/%0aalert(document.domain)//"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert response.status_code == 400
