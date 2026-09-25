"""E2E: the Lemma MCP surfaces, over the transports their callers really use.

The pod MCP endpoint is driven with an actual MCP client (the full
``initialize`` -> ``notifications/initialized`` -> ``tools/list`` ->
``tools/call`` handshake) over the wire against a real backend server.

A conversation's tools are reached only by a local agent, through the Agent
Host: its MCP bridge relays each call as an ``mcp`` frame on the host's link
WebSocket, and waits on a parked interaction with ``interaction_wait``. Those
tests drive the link. The conversation's HTTP MCP mount they used to drive is
gone, because the Agent Host was its only caller; what they prove about
authorization -- the run's token, the agent's own grants, pod membership --
moved with the tools onto the link and is asserted there.

They guard the regression where the FastMCP apps were built with
``stateless_http=False``: the server then held the ``Mcp-Session-Id`` session in
the memory of whichever worker handled ``initialize``, and the follow-up
``initialized`` notification landing on a different worker/replica got a
``404 Not Found`` ("session expired"). Codex's rmcp client read that as a fatal
transport error and aborted ``thread/start``. ``test_mcp_endpoint_is_stateless``
asserts the property that fixes it (no server-held session), and the behavioral
tests confirm a real client completes the handshake and calls tools.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import status
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from app.modules.agent.tests.e2e.agent_host_helpers import (
    LinkMcpClient,
    LinkMcpError,
    app_of,
    connected_host,
    pair,
)

pytestmark = pytest.mark.e2e


def _pod_mcp_url(backend_server, pod_id: str) -> str:
    base = backend_server["host_base_url"].rstrip("/")
    return f"{base}/agent-runtime/pods/{pod_id}/mcp"


@asynccontextmanager
async def _conversation_tools(client, conversation_id: str, token: str):
    """A paired host's MCP bridge, relaying for one conversation's run token."""
    machine = await pair(client, client, display_name="e2e mcp bridge")
    link = await connected_host(app_of(client), machine)
    try:
        yield LinkMcpClient(link, conversation_id=conversation_id, token=token)
    finally:
        await link.aclose()


def _mcp_client(url: str, token: str) -> Client:
    return Client(
        StreamableHttpTransport(
            url=url,
            headers={"Authorization": f"Bearer {token}"},
        )
    )


async def _create_pod(authenticated_client, fixed_test_org) -> str:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"mcp-client-{uuid4().hex[:8]}",
            "description": "MCP client e2e",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _create_table(authenticated_client, pod_id: str, table_name: str) -> None:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": table_name,
            "primary_key_column": "id",
            "enable_rls": False,
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "title", "type": "TEXT", "required": True},
            ],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


@pytest.mark.asyncio
async def test_pod_mcp_client_lists_and_calls_tools(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    backend_server,
):
    """A real MCP client completes the handshake against the pod MCP surface and
    drives ``lemma_pod_write_record`` / ``lemma_pod_get_records`` end to end."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    table = f"notes_{uuid4().hex[:8]}"
    await _create_table(authenticated_client, pod_id, table)

    url = _pod_mcp_url(backend_server, pod_id)
    async with _mcp_client(url, fixed_test_user["token"]) as client:
        # Handshake (initialize + initialized) completed inside __aenter__; if the
        # server demanded session affinity it would have 404'd here.
        tools = await client.list_tools()
        tool_names = {tool.name for tool in tools}
        assert "lemma_pod_write_record" in tool_names, tool_names
        assert "lemma_pod_get_records" in tool_names, tool_names

        created = await client.call_tool(
            "lemma_pod_write_record",
            {"action": "create", "table_name": table, "data": {"title": "from-mcp"}},
        )
        assert created.is_error is False, created.content
        assert created.structured_content["success"] is True, created.structured_content

        listed = await client.call_tool(
            "lemma_pod_get_records",
            {"table_name": table},
        )
        assert listed.is_error is False, listed.content
        titles = [
            record.get("title") for record in listed.structured_content["records"]
        ]
        assert "from-mcp" in titles, listed.structured_content


@pytest.mark.asyncio
async def test_pod_mcp_rejects_missing_and_bad_token(
    authenticated_client,
    fixed_test_org,
    backend_server,
):
    """The pod MCP surface refuses unauthenticated/garbage tokens at the
    handshake, rather than exposing tools."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    url = _pod_mcp_url(backend_server, pod_id)

    with pytest.raises(Exception):  # noqa: B017 - any auth failure aborts the client
        async with _mcp_client(url, "not-a-real-token") as client:
            await client.list_tools()


@pytest.mark.asyncio
async def test_conversation_mcp_client_lists_and_calls_tools(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    """A tool call relayed on the link sees the agent's ``lemma_`` tools, and a
    call routes through to a structured result (not a transport error)."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    table = f"notes_{uuid4().hex[:8]}"
    await _create_table(authenticated_client, pod_id, table)

    agent_name = f"reader_{uuid4().hex[:8]}"
    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Answer briefly.",
            "toolsets": ["POD"],
        },
    )
    assert create_agent.status_code == status.HTTP_201_CREATED, create_agent.text

    create_conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": agent_name, "title": "MCP client e2e"},
    )
    assert create_conversation.status_code == status.HTTP_201_CREATED, (
        create_conversation.text
    )
    conversation_id = create_conversation.json()["id"]

    async with _conversation_tools(
        authenticated_client, conversation_id, fixed_test_user["token"]
    ) as client:
        tools = (await client.list_tools()).tools
        tool_names = {tool.name for tool in tools}
        assert "lemma_pod_get_records" in tool_names, tool_names
        # Every exposed tool carries the lemma_ prefix this surface promises.
        assert all(name.startswith("lemma_") for name in tool_names), tool_names

        # The call routes through the dispatcher and comes back as a structured
        # MCP tool result — proving the over-the-wire path works, regardless of
        # whether the named agent's grant lets the read succeed.
        result = await client.call_tool(
            "lemma_pod_get_records",
            {"table_name": table},
        )
        assert result.structured_content is not None, result.content
        assert isinstance(result.structured_content, dict), result.structured_content


@pytest.mark.asyncio
async def test_mcp_endpoint_is_stateless(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    backend_server,
):
    """Regression guard for the Codex ``thread/start`` failure.

    With ``stateless_http=False`` the server returned an ``Mcp-Session-Id`` on
    ``initialize`` and then required every later request to carry it and hit the
    same worker, 404'ing the follow-up ``initialized`` notification otherwise.
    In stateless mode there is no server-held session: ``initialize`` returns no
    session header, and a ``tools/list`` issued with no prior session still
    succeeds. Both are asserted here at the raw JSON-RPC layer, in a single
    process, so the check is meaningful even though the test server runs one
    worker (the stateful 404 surfaces across workers/replicas in production).
    """
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    url = _pod_mcp_url(backend_server, pod_id)
    headers = {
        "Authorization": f"Bearer {fixed_test_user['token']}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    async with httpx.AsyncClient(timeout=30) as http:
        initialize = await http.post(
            url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "lemma-e2e", "version": "1.0.0"},
                },
            },
        )
        assert initialize.status_code == status.HTTP_200_OK, initialize.text
        # Stateless transport assigns no session, so there is nothing to expire.
        assert "mcp-session-id" not in initialize.headers, dict(initialize.headers)

        # A fresh request carrying no session id still works — impossible in the
        # old stateful mode, which would answer 400/404.
        list_tools = await http.post(
            url,
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert list_tools.status_code == status.HTTP_200_OK, list_tools.text
        body = list_tools.json()
        names = {tool["name"] for tool in body["result"]["tools"]}
        assert "lemma_pod_get_records" in names, body


@pytest.mark.asyncio
async def test_the_bridge_waits_on_a_parked_interaction_until_it_is_decided(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    db_session,
):
    """What the host's MCP bridge does while it holds a tool response open.

    `ask_user` and `request_approval` hand a remote harness a parked tool call id
    instead of prose. This is the other end of that: the bridge sends
    ``interaction_wait`` with the id and is answered once a person decides --
    and the answer is returned as the tool's own result, so the model waits
    inside its turn exactly as it does for a native ACP permission.

    Nothing is stored to make this work. Deciding an interaction already writes a
    synthesized tool RETURN under the same durable id, so "decided" is simply
    "that return exists". The bridge used to poll a route every two seconds for
    it; the wait is now answered by Lemma, woken by the conversation's own
    realtime frame, with a slow re-check under it.
    """
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    created = await authenticated_client.post(
        f"/pods/{pod_id}/conversations", json={"title": "parked"}
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    conversation_id = created.json()["id"]
    tool_call_id = f"parked-{uuid4().hex[:8]}"

    machine = await pair(
        authenticated_client, authenticated_client, display_name="e2e parked"
    )
    link = await connected_host(app_of(authenticated_client), machine)
    try:
        # A token that is not this conversation's must not be able to wait on it.
        refused = await link.request(
            "interaction_wait",
            {
                "conversation_id": conversation_id,
                "token": "nope",
                "tool_call_id": tool_call_id,
            },
        )
        assert refused["type"] == "error", refused
        assert refused["body"]["code"] == "UNAUTHORIZED", refused

        waiting = link.send(
            "interaction_wait",
            {
                "conversation_id": conversation_id,
                "token": fixed_test_user["token"],
                "tool_call_id": tool_call_id,
            },
        )
        # Pending is not an answer: nothing comes back until someone decides.
        with pytest.raises(TimeoutError):
            await link.answer_to(waiting, timeout=1.0)

        # The person decides. This is the same write the approvals endpoint
        # makes; it publishes nothing, so the re-check is what finds it here.
        from app.modules.agent.domain.value_objects import MessageKind, MessageRole
        from app.modules.agent.infrastructure.models import MessageModel

        db_session.add(
            MessageModel(
                conversation_id=UUID(conversation_id),
                role=MessageRole.TOOL.value,
                kind=MessageKind.TOOL_RETURN.value,
                tool_call_id=tool_call_id,
                tool_name="ask_user",
                tool_result={"success": True, "answers": {"Pick one": "Blue"}},
                sequence=1,
            )
        )
        await db_session.commit()

        decided = await link.answer_to(waiting, timeout=30)
    finally:
        await link.aclose()

    assert decided["type"] == "interaction_ok", decided
    assert decided["body"]["answer"]["answers"] == {"Pick one": "Blue"}


async def _create_agent(authenticated_client, pod_id: str) -> dict:
    agent_name = f"narrow_{uuid4().hex[:8]}"
    response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Answer briefly.",
            "toolsets": ["POD"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _create_conversation(
    client, pod_id: str, agent_name: str, *, headers: dict | None = None
) -> str:
    response = await client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": agent_name, "title": "MCP authorization e2e"},
        **({"headers": headers} if headers else {}),
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


@pytest.mark.asyncio
async def test_conversation_mcp_authorizes_as_the_agent_not_as_its_caller(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    """A named agent's pod tool call is bounded by the agent's own grants.

    The context this mount builds by hand used to omit ``workload_id``, and
    ``pod_data_access`` reads an unset one as "the pod default assistant" -- which
    runs with the *invoking user's* pod permissions. So on every Agent Host run a
    narrowly granted agent wrote tables it was never granted, purely because the
    person driving the harness could. PS-AGENT-002: an agent gets no more than it
    was granted.
    """
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    table = f"orders_{uuid4().hex[:8]}"
    await _create_table(authenticated_client, pod_id, table)
    agent = await _create_agent(authenticated_client, pod_id)
    conversation_id = await _create_conversation(
        authenticated_client, pod_id, agent["name"]
    )

    async with _conversation_tools(
        authenticated_client, conversation_id, fixed_test_user["token"]
    ) as client:
        result = await client.call_tool(
            "lemma_pod_write_record",
            {"action": "create", "table_name": table, "data": {"title": "blocked"}},
        )
    denied = result.structured_content
    assert isinstance(denied, dict), result.content
    assert denied["success"] is False, denied
    assert denied["code"] == "MISSING_WORKLOAD_RESOURCE_GRANT", denied
    assert denied["needs_approval"] is True, denied


@pytest.mark.asyncio
async def test_conversation_mcp_refuses_a_member_removed_from_the_pod(
    authenticated_client,
    async_client,
    fixed_test_org,
):
    """Owning a conversation is not access to the pod it lives in.

    Every HTTP conversation route asserts pod membership; the conversation MCP
    asserted only that the token's user id matched ``conversation.user_id``.
    The check is per call, so a link already open stops working the moment the
    membership goes. Ownership
    survives being removed from a pod, so a removed member kept a working path to
    run that pod's agent tools (PS-POD-040, DEV-ACCESS-001).
    """
    from app.modules.test_support.e2e_authz import (
        add_pod_member,
        auth_headers,
        invite_org_member,
        signup_user,
    )

    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    agent = await _create_agent(authenticated_client, pod_id)

    member = await signup_user(async_client, "mcp-membership")
    org_member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=member,
    )
    pod_member = await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=org_member["id"],
        role="POD_EDITOR",
    )
    conversation_id = await _create_conversation(
        async_client, pod_id, agent["name"], headers=auth_headers(member)
    )

    async with _conversation_tools(
        async_client, conversation_id, member["token"]
    ) as client:
        assert (await client.list_tools()).tools

        removed = await authenticated_client.delete(
            f"/pods/{pod_id}/members/{pod_member['pod_member_id']}"
        )
        assert removed.status_code == status.HTTP_204_NO_CONTENT, removed.text

        with pytest.raises(LinkMcpError) as refused:
            await client.list_tools()
        assert refused.value.code == "UNAUTHORIZED"
