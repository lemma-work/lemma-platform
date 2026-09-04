"""Discover connector operations from an external MCP server's tool list.

The install's ``session_setup`` is replayed first, so tools a server only
exposes after a session-scoped call are discovered like any other.
"""

from __future__ import annotations

from typing import Any

from app.core.net.url_guard import UnsafeUrlError, assert_safe_url
from app.modules.connectors.infrastructure.adapters.mcp_executor import (
    McpClientFactory,
    apply_session_setup,
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
    # Guard the target before connecting, as execution does. Discovery runs at
    # install time behind the install-time guard, but a client_factory or a
    # later re-discovery could reach here on its own; a bare SSRF hole in a
    # "just list the tools" path is still an SSRF hole.
    try:
        await assert_safe_url(str(server_url))
    except UnsafeUrlError as exc:
        raise ValueError(f"Refusing to reach an unsafe MCP target: {exc}") from exc
    headers = build_mcp_headers(connection_config, credentials)
    factory = client_factory or default_mcp_client_factory

    client = factory(server_url, headers, timeout_seconds)
    async with client:
        # Before `list_tools`, not after: the install's setup calls are what
        # unlock a server's session-gated tools, and a tool that is not in this
        # list is never stored as an operation and so can never be called by
        # name. Discovering against a virgin session is why those tools were
        # unreachable even when the setup call itself was.
        await apply_session_setup(client, connection_config)
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
