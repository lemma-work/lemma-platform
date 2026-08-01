"""Discover connector operations from an external MCP server's tool list."""

from __future__ import annotations

from typing import Any

from app.modules.connectors.infrastructure.adapters.mcp_executor import (
    McpClientFactory,
    build_mcp_headers,
    default_mcp_client_factory,
)
from app.modules.connectors.services.discovery.base import (
    DiscoveredOperation,
    normalize_operation_name,
)


async def discover_mcp(
    *,
    connection_config: dict[str, Any],
    credentials: dict[str, Any] | None = None,
    client_factory: McpClientFactory | None = None,
    timeout_seconds: float | None = None,
) -> list[DiscoveredOperation]:
    """Connect to the MCP server and map each tool to a discovered operation."""
    server_url = (connection_config or {}).get("server_url")
    if not server_url:
        raise ValueError("MCP discovery requires 'server_url' in connection config.")
    headers = build_mcp_headers(connection_config, credentials)
    factory = client_factory or default_mcp_client_factory

    client = factory(server_url, headers, timeout_seconds)
    async with client:
        tools = await client.list_tools()

    operations: list[DiscoveredOperation] = []
    for tool in tools:
        tool_name = getattr(tool, "name", None)
        if not tool_name:
            continue
        input_schema = getattr(tool, "inputSchema", None) or {"type": "object"}
        output_schema = getattr(tool, "outputSchema", None)
        operations.append(
            DiscoveredOperation(
                name=normalize_operation_name(tool_name),
                display_name=tool_name,
                description=getattr(tool, "description", None) or tool_name,
                input_schema=input_schema,
                output_schema=output_schema,
                execution={"kind": "mcp", "tool_name": tool_name},
            )
        )
    return operations
