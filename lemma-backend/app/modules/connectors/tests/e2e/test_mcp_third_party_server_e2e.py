"""Our MCP client against a server nobody here wrote.

The other MCP tests run `fastmcp` in-process. That is a real server over real
HTTP, and it proves the transport -- but both ends of the conversation come from
the same library, so anything the two happen to agree on is invisible. A
third-party server is the only thing that shows whether we speak MCP or speak
fastmcp.

Context7 is used because it needs no credential, so this stays runnable rather
than becoming a test nobody can execute. Marked `provider`: it depends on
somebody else's uptime and is excluded from the hermetic suite.

It has already earned its place. Context7 names its tools with hyphens, which
the discoverer normalises to underscores for the catalog while keeping the real
name in the execution descriptor. Nothing in-process exercises that round trip,
because fastmcp tools are named in Python and arrive underscored already.
"""

from __future__ import annotations

import os

import pytest

from app.modules.connectors.infrastructure.adapters.mcp_executor import McpExecutor
from app.modules.connectors.services.discovery.mcp_discoverer import discover_mcp

pytestmark = [pytest.mark.e2e, pytest.mark.provider, pytest.mark.asyncio]

_SERVER = os.getenv("LEMMA_E2E_MCP_URL", "https://mcp.context7.com/mcp")
_CONFIG = {"server_url": _SERVER}
_TIMEOUT = 45.0


async def _discover():
    """Discover, or skip when the third party is simply unreachable.

    Only a transport failure skips. A protocol or contract mismatch is a real
    failure and must stay one -- that is the entire reason this test exists.
    """
    import httpx

    try:
        return await discover_mcp(
            connection_config=_CONFIG, credentials=None, timeout_seconds=_TIMEOUT
        )
    except* (httpx.ConnectError, httpx.ConnectTimeout, OSError) as group:
        pytest.skip(f"{_SERVER} unreachable: {group.exceptions[0]!r}")


@pytest.fixture(scope="module")
async def tools():
    found = await _discover()
    return {item.name: item for item in found}


class TestDiscoveryAgainstARealServer:
    async def test_the_servers_tools_are_found(self, tools):
        assert tools, "a live MCP server published no tools"
        assert "resolve_library_id" in tools

    async def test_a_hyphenated_tool_name_is_normalised_for_the_catalog(self, tools):
        """An operation name becomes part of a URL and an agent's vocabulary, so
        it is normalised -- but the server still has to be called by the name it
        chose."""
        operation = tools["resolve_library_id"]
        assert operation.name == "resolve_library_id"
        assert operation.execution["tool_name"] == "resolve-library-id"

    async def test_the_published_schema_survives_discovery(self, tools):
        schema = tools["resolve_library_id"].input_schema or {}
        assert "query" in schema.get("properties", {})

    async def test_descriptions_survive_discovery(self, tools):
        assert (tools["resolve_library_id"].description or "").strip()


class TestExecutionAgainstARealServer:
    async def test_a_tool_call_returns_the_servers_answer(self, tools):
        operation = tools["resolve_library_id"]
        result = await McpExecutor().execute(
            connector_id="mcp",
            operation_name=operation.name,
            execution=operation.execution,
            payload={"query": "react hooks", "libraryName": "react"},
            third_party_credentials=None,
            connection_config=_CONFIG,
            deadline_seconds=_TIMEOUT,
        )
        # Whatever the server chooses to say, it has to arrive as content we
        # turned into something an agent can read.
        assert isinstance(result, dict)
        assert str(result).strip()
