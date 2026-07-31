"""Unit tests for the MCP executor + discoverer (in-memory fastmcp server)."""

from __future__ import annotations

import pytest
from fastmcp import Client, FastMCP

from app.modules.connectors.domain.errors import OperationExecutionValidationError
from app.modules.connectors.infrastructure.adapters.mcp_executor import (
    McpExecutor,
    build_mcp_headers,
)
from app.modules.connectors.services.discovery.mcp_discoverer import discover_mcp


def _server() -> FastMCP:
    mcp = FastMCP("test-server")

    @mcp.tool
    def add(a: int, b: int) -> dict:
        """Add two numbers."""
        return {"sum": a + b}

    @mcp.tool
    def greet(name: str) -> dict:
        """Greet someone."""
        return {"message": f"hello {name}"}

    return mcp


_SERVER = _server()


def _factory(server_url, headers):
    return Client(_SERVER)


CONN = {"server_url": "memory://test"}


@pytest.mark.asyncio
async def test_mcp_execute_returns_structured_result():
    ex = McpExecutor(client_factory=_factory)
    result = await ex.execute(
        connector_id="mcp",
        operation_name="add",
        execution={"kind": "mcp", "tool_name": "add"},
        payload={"a": 2, "b": 3},
        third_party_credentials=None,
        connection_config=CONN,
    )
    assert result == {"sum": 5}


@pytest.mark.asyncio
async def test_mcp_execute_uses_tool_name_from_descriptor():
    ex = McpExecutor(client_factory=_factory)
    result = await ex.execute(
        connector_id="mcp",
        operation_name="op_alias",  # differs from the tool name
        execution={"kind": "mcp", "tool_name": "greet"},
        payload={"name": "ada"},
        third_party_credentials=None,
        connection_config=CONN,
    )
    assert result == {"message": "hello ada"}


@pytest.mark.asyncio
async def test_mcp_execute_requires_server_url():
    ex = McpExecutor(client_factory=_factory)
    # The error message is fixed by design (domain/errors.py); assert on the type.
    with pytest.raises(OperationExecutionValidationError):
        await ex.execute(
            connector_id="mcp",
            operation_name="add",
            execution={"kind": "mcp", "tool_name": "add"},
            payload={},
            third_party_credentials=None,
            connection_config={},
        )


@pytest.mark.asyncio
async def test_mcp_discover_maps_tools_to_operations():
    ops = await discover_mcp(connection_config=CONN, client_factory=_factory)
    by_name = {o.name: o for o in ops}
    assert set(by_name) == {"add", "greet"}
    assert by_name["add"].execution == {"kind": "mcp", "tool_name": "add"}
    assert by_name["add"].input_schema["type"] == "object"
    assert "a" in by_name["add"].input_schema.get("properties", {})
    assert by_name["greet"].description


def test_build_mcp_headers_prefers_bearer_token():
    h = build_mcp_headers({"extra_headers": {"X-Env": "prod"}}, {"bearer_token": "t"})
    assert h["Authorization"] == "Bearer t"
    assert h["X-Env"] == "prod"
    # falls back to access_token, then connection_config bearer_token
    assert build_mcp_headers({}, {"access_token": "a"})["Authorization"] == "Bearer a"
    assert build_mcp_headers({"bearer_token": "c"}, None)["Authorization"] == "Bearer c"
    assert build_mcp_headers({}, None) == {}
