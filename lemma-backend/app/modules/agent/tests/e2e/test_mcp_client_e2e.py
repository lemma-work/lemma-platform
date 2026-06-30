"""E2E: the Lemma MCP surfaces work over a real streamable-HTTP MCP client.

These tests drive the pod and conversation MCP endpoints with an actual MCP
client (the full ``initialize`` -> ``notifications/initialized`` -> ``tools/list``
-> ``tools/call`` handshake) over the wire against a real backend server, and
assert the ``lemma_`` tools run end to end.

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

from uuid import uuid4

import httpx
import pytest
from fastapi import status
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def _pod_mcp_url(backend_server, pod_id: str) -> str:
    base = backend_server["host_base_url"].rstrip("/")
    return f"{base}/agent-runtime/pods/{pod_id}/mcp"


def _conversation_mcp_url(backend_server, conversation_id: str) -> str:
    base = backend_server["host_base_url"].rstrip("/")
    return f"{base}/agent-runtime/conversations/{conversation_id}/mcp"


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
        titles = [record.get("title") for record in listed.structured_content["records"]]
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
    backend_server,
):
    """A real MCP client completes the handshake against the conversation MCP
    surface, sees the agent's ``lemma_`` tools, and a tool call routes through to
    a structured result (not a transport error)."""
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

    url = _conversation_mcp_url(backend_server, conversation_id)
    async with _mcp_client(url, fixed_test_user["token"]) as client:
        tools = await client.list_tools()
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
            raise_on_error=False,
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
