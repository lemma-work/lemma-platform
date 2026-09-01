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


class _TextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _ToolError:
    is_error = True

    def __init__(self, text: str) -> None:
        self.content = [_TextBlock(text)]


def test_an_mcp_tool_error_reaches_the_caller_with_what_the_tool_said():
    """An agent given "Connector provider is temporarily unavailable" cannot
    correct itself and has every reason to retry. The tool told it exactly what
    was wrong."""
    with pytest.raises(OperationExecutionValidationError) as caught:
        McpExecutor()._map_result(
            "create_issue", _ToolError('project key "FOO" does not exist')
        )

    assert caught.value.details == {
        "upstream_message": 'project key "FOO" does not exist'
    }


def test_an_mcp_tool_error_does_not_open_the_breaker():
    """Five bad-argument calls used to disable a healthy MCP server for the
    whole organization."""
    with pytest.raises(Exception) as caught:
        McpExecutor()._map_result("create_issue", _ToolError("bad argument"))

    assert not isinstance(caught.value, BREAKER_COUNTS)
