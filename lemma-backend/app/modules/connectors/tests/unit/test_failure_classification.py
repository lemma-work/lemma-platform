"""Which failures are the provider's fault, and which ones the breaker counts.

Two mirror-image bugs. For http/sql the map stopped at 422, so every provider
5xx fell through to the catch-all as our own 500 -- the breaker, whose whole
purpose is "a provider that is down, where every caller waits the full
timeout", could open on a refused connection but never on a provider answering
503 to everything. For MCP the opposite: an application-level tool error was
raised as an infrastructure error, so five bad-argument calls inside the window
disabled a healthy server for the whole organization, and the tool's
explanation was discarded by an error class that hardcodes its own message.
"""

from __future__ import annotations

import httpx
import pytest

from app.modules.connectors.domain.errors import (
    OperationExecutionInfrastructureError,
    OperationExecutionNotFoundError,
    OperationExecutionRateLimitedError,
    OperationExecutionTimeoutError,
    OperationExecutionValidationError,
)
from app.modules.connectors.infrastructure.adapters.mcp_executor import McpExecutor
from app.modules.connectors.services.execution.plumbing import (
    execution_failures_translated,
)

# What `ConnectorOperationUseCases` records against the breaker.
BREAKER_COUNTS = (OperationExecutionInfrastructureError, OperationExecutionTimeoutError)


def _translated(status: int) -> Exception:
    request = httpx.Request("GET", "https://provider.example/thing")
    response = httpx.Response(status, request=request, text="provider said so")
    with pytest.raises(Exception) as caught:
        with execution_failures_translated():
            raise httpx.HTTPStatusError("boom", request=request, response=response)
    return caught.value


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_a_provider_outage_is_an_outage_and_opens_the_breaker(status):
    error = _translated(status)
    assert isinstance(error, OperationExecutionInfrastructureError)
    assert isinstance(error, BREAKER_COUNTS), (
        "the breaker exists for exactly this and could not see it"
    )


def test_being_rate_limited_is_not_an_outage_and_must_not_open_the_breaker():
    """The provider is healthy and answering; the caller is asking too often.
    Counting it would let one busy caller disable an operation for everyone
    sharing that provider -- the opposite of what a rate limit asks for."""
    error = _translated(429)
    assert isinstance(error, OperationExecutionRateLimitedError)
    assert not isinstance(error, BREAKER_COUNTS)


def test_a_status_the_provider_chose_is_still_an_answer():
    """The classifications that already worked must keep working -- a 404 is a
    normal branch, not a failure of the connector."""
    error = _translated(404)
    assert isinstance(error, OperationExecutionNotFoundError)
    assert not isinstance(error, BREAKER_COUNTS)


@pytest.fixture
def refusing_server():
    """An in-memory MCP server whose tool refuses the call it is given.

    Deliberately a real server driven through `McpExecutor.execute`, not a
    hand-built result handed to `_map_result`. The stand-in this replaces
    certified a path production never took: fastmcp's `call_tool` defaults to
    `raise_on_error=True`, so a refusing tool raised `ToolError` -- which
    `_is_transport_failure` accepts -- long before `_map_result` was reached.
    The classification tested green while every MCP tool error in production
    was reported as a provider outage.
    """
    from fastmcp import Client, FastMCP
    from fastmcp.exceptions import ToolError as ServerToolError

    server = FastMCP("refusing-server")

    @server.tool
    def create_issue(project_key: str) -> dict:
        """Create an issue."""
        raise ServerToolError(f'project key "{project_key}" does not exist')

    def factory(server_url, headers, timeout=None):
        return Client(server)

    return factory


async def _execute_create_issue(factory) -> Exception:
    with pytest.raises(Exception) as caught:
        await McpExecutor(client_factory=factory).execute(
            connector_id="mcp",
            operation_name="create_issue",
            execution={"kind": "mcp", "tool_name": "create_issue"},
            payload={"project_key": "FOO"},
            third_party_credentials=None,
            connection_config={"server_url": "https://mcp.example.test/mcp"},
        )
    return caught.value


@pytest.mark.asyncio
async def test_an_mcp_tool_error_reaches_the_caller_with_what_the_tool_said(
    refusing_server,
):
    """An agent given "Connector provider is temporarily unavailable" cannot
    correct itself and has every reason to retry. The tool told it exactly what
    was wrong."""
    error = await _execute_create_issue(refusing_server)

    assert isinstance(error, OperationExecutionValidationError)
    assert error.details["upstream_message"] == 'project key "FOO" does not exist'
    assert error.details["operation_name"] == "create_issue"
    assert error.details["reason"] == "tool_error"


@pytest.mark.asyncio
async def test_an_mcp_tool_error_does_not_open_the_breaker(refusing_server):
    """Five bad-argument calls used to disable a healthy MCP server for the
    whole organization."""
    error = await _execute_create_issue(refusing_server)

    assert not isinstance(error, BREAKER_COUNTS)


@pytest.mark.asyncio
async def test_a_session_setup_step_the_server_refuses_names_itself():
    """A half-applied preamble leaves a session whose tool list is neither the
    configured one nor the default. Reporting it as the operation failing sends
    the reader to the wrong call."""
    from fastmcp import Client, FastMCP
    from fastmcp.exceptions import ToolError as ServerToolError

    server = FastMCP("gated-server")

    @server.tool
    def enable_tool_group(group: str) -> dict:
        """Enable a group of tools."""
        raise ServerToolError(f"Unknown tool group '{group}'.")

    @server.tool
    def ping() -> dict:
        """Answer."""
        return {"ok": True}

    with pytest.raises(OperationExecutionValidationError) as caught:
        await McpExecutor(client_factory=lambda *a, **k: Client(server)).execute(
            connector_id="mcp",
            operation_name="ping",
            execution={"kind": "mcp", "tool_name": "ping"},
            payload={},
            third_party_credentials=None,
            connection_config={
                "server_url": "https://mcp.example.test/mcp",
                "session_setup": [
                    {"tool_name": "enable_tool_group", "arguments": {"group": "nope"}}
                ],
            },
        )

    details = caught.value.details
    assert details["reason"] == "session_setup_failed"
    assert details["operation_name"] == "enable_tool_group"
    assert "Unknown tool group" in details["upstream_message"]
    assert not isinstance(caught.value, BREAKER_COUNTS)
