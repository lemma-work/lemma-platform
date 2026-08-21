"""The MCP connector against a real MCP server over real HTTP.

The unit tests drive a hand-written fake client, so they prove our mapping logic
but not that we speak the protocol. This runs an actual ``fastmcp`` server on a
real socket and goes through the streamable-HTTP transport, which is what
catches the things a fake cannot: tool schemas as the protocol actually delivers
them, structured versus unstructured content, how a tool error arrives on the
wire, and whether our deadline is really attached to the connection.

The server is in-process and binds an ephemeral port, so this is hermetic and
needs nothing external.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import socket
from typing import Any

import pytest
import pytest_asyncio

from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.domain.connector_operation import ResolvedOperation
from app.modules.connectors.domain.errors import (
    OperationExecutionInfrastructureError,
    OperationExecutionTimeoutError,
)
from app.modules.connectors.infrastructure.adapters.mcp_executor import McpExecutor
from app.modules.connectors.infrastructure.kinds import build_kind_registry
from app.modules.connectors.services.discovery.mcp_discoverer import discover_mcp
from app.modules.connectors.services.execution import KindDispatcher
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _build_server():
    from fastmcp import FastMCP

    server = FastMCP("lemma-e2e")

    @server.tool
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    @server.tool
    def greet(name: str) -> str:
        """Return a greeting."""
        return f"hello {name}"

    @server.tool
    def lookup(customer_id: str) -> dict:
        """Return a structured record."""
        return {"customer_id": customer_id, "plan": "pro", "seats": 12}

    @server.tool
    def render_badge() -> Any:
        """Return an image, exercising the binary content path."""
        from fastmcp.utilities.types import Image

        return Image(data=_PNG, format="png")

    @server.tool
    def explode() -> str:
        """Always fails, so the error path is real rather than simulated."""
        raise ValueError("tool blew up")

    @server.tool
    async def stall() -> str:
        """Never returns, so the deadline has something to bite on."""
        await asyncio.sleep(300)
        return "unreachable"

    return server


@pytest_asyncio.fixture(scope="module")
async def mcp_server():
    """A real MCP server on a real port for the duration of the module."""
    server = _build_server()
    port = _free_port()
    task = asyncio.create_task(
        server.run_async(
            transport="http", host="127.0.0.1", port=port, show_banner=False
        )
    )

    url = f"http://127.0.0.1:{port}/mcp"

    async def probe() -> None:
        if task.done():
            raise RuntimeError(f"MCP server failed to start: {task.exception()}")
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        await writer.wait_closed()

    # Wait for the listener rather than sleeping a fixed amount, so the suite is
    # not timing-dependent on a loaded machine. retry_exceptions=(OSError,):
    # the port not listening yet is the expected "not ready" case. A crashed
    # server task instead raises RuntimeError from inside probe(), which is
    # not in retry_exceptions and so propagates immediately, same as the
    # original loop's eager task.done() check. interval kept at the original
    # 0.05s (already tighter than the usual 0.15s default) since this is a
    # hot local port check.
    await eventually(
        label=f"MCP server on port {port} to start listening",
        probe=probe,
        done=lambda _: True,
        retry_exceptions=(OSError,),
        timeout_seconds=5.0,
        interval_seconds=0.05,
    )

    yield url

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.fixture
def connection_config(mcp_server):
    return {"server_url": mcp_server}


async def _call(connection_config, tool: str, payload: dict | None = None, **kwargs):
    return await McpExecutor().execute(
        connector_id="mcp",
        operation_name=tool,
        execution={"kind": "mcp", "tool_name": tool},
        payload=payload or {},
        third_party_credentials=None,
        connection_config=connection_config,
        **kwargs,
    )


class TestDiscovery:
    async def test_discovery_lists_the_servers_real_tools(self, connection_config):
        found = await discover_mcp(connection_config=connection_config)
        names = {op.name for op in found}
        assert {"add", "greet", "lookup", "render_badge"} <= names

    async def test_discovered_schemas_come_from_the_server(self, connection_config):
        found = await discover_mcp(connection_config=connection_config)
        add = next(op for op in found if op.name == "add")
        properties = add.input_schema["properties"]
        assert set(properties) == {"a", "b"}
        assert properties["a"]["type"] == "integer"

    async def test_the_execution_descriptor_keeps_the_servers_own_tool_name(
        self, connection_config
    ):
        found = await discover_mcp(connection_config=connection_config)
        add = next(op for op in found if op.name == "add")
        # The public name is normalized; execution must still address the tool
        # by whatever the server actually calls it.
        assert add.execution == {"kind": "mcp", "tool_name": "add"}

    async def test_descriptions_survive_discovery(self, connection_config):
        found = await discover_mcp(connection_config=connection_config)
        greet = next(op for op in found if op.name == "greet")
        assert "greeting" in (greet.description or "").lower()


class TestExecution:
    async def test_a_scalar_result_round_trips(self, connection_config):
        assert await _call(connection_config, "add", {"a": 2, "b": 3}) == {"result": 5}

    async def test_a_text_result_round_trips(self, connection_config):
        assert await _call(connection_config, "greet", {"name": "ada"}) == {
            "result": "hello ada"
        }

    async def test_a_structured_result_is_returned_as_an_object(
        self, connection_config
    ):
        result = await _call(connection_config, "lookup", {"customer_id": "c-1"})
        assert result["plan"] == "pro"
        assert result["seats"] == 12

    async def test_an_image_result_becomes_binary_content(self, connection_config):
        result = await _call(connection_config, "render_badge")
        payload = result.model_dump() if hasattr(result, "model_dump") else result
        assert payload["type"] == "binary_content"
        assert base64.b64decode(payload["content_base64"]) == _PNG

    async def test_a_failing_tool_becomes_a_domain_error(self, connection_config):
        with pytest.raises(OperationExecutionInfrastructureError):
            await _call(connection_config, "explode")

    async def test_calling_a_tool_that_does_not_exist_fails_cleanly(
        self, connection_config
    ):
        with pytest.raises(OperationExecutionInfrastructureError):
            await _call(connection_config, "no_such_tool")

    async def test_an_unreachable_server_fails_rather_than_hanging(self):
        # A closed port on localhost: the connection is refused immediately, so
        # this asserts the error mapping, not the timeout.
        with pytest.raises(OperationExecutionInfrastructureError):
            await _call({"server_url": f"http://127.0.0.1:{_free_port()}/mcp"}, "add")


class TestDeadlines:
    async def test_a_stalled_tool_is_cut_off_by_its_deadline(self, connection_config):
        # Before the deadline was threaded through, fastmcp had no timeout of its
        # own and this would hang until the caller gave up.
        with pytest.raises(OperationExecutionInfrastructureError):
            await asyncio.wait_for(
                _call(connection_config, "stall", deadline_seconds=2.0), timeout=20
            )

    async def test_discovery_is_bounded_by_the_dispatcher(self, connection_config):
        from unittest.mock import AsyncMock
        from uuid import uuid4

        from app.modules.connectors.config import connector_settings
        from app.modules.connectors.domain.auth_config import AuthConfigSource
        from app.modules.connectors.domain.connector import McpKindSpec
        from app.modules.connectors.domain.kinds import ResolvedInstall

        dispatcher = KindDispatcher(
            build_kind_registry(
                composio_gateway=AsyncMock(), package_gateway=AsyncMock()
            )
        )
        install = ResolvedInstall(
            connector_id="mcp",
            kind=ConnectorKind.MCP,
            auth_config_id=uuid4(),
            organization_id=uuid4(),
            # A routable address that never answers, so the deadline is what ends it.
            config={"server_url": "http://10.255.255.1:9/mcp"},
            config_source=AuthConfigSource.SYSTEM_DEFAULT,
            spec=McpKindSpec(),
        )
        original = connector_settings.connector_discovery_timeout_seconds
        connector_settings.connector_discovery_timeout_seconds = 2.0
        try:
            with pytest.raises(OperationExecutionTimeoutError):
                await asyncio.wait_for(dispatcher.discover(install), timeout=25)
        finally:
            connector_settings.connector_discovery_timeout_seconds = original


class TestThroughTheDispatcher:
    async def test_mcp_kind_discovers_and_executes_end_to_end(self, connection_config):
        from unittest.mock import AsyncMock
        from uuid import uuid4

        from app.modules.connectors.domain.auth_config import AuthConfigSource
        from app.modules.connectors.domain.connector import McpKindSpec
        from app.modules.connectors.domain.kinds import ResolvedInstall

        dispatcher = KindDispatcher(
            build_kind_registry(
                composio_gateway=AsyncMock(), package_gateway=AsyncMock()
            )
        )
        install = ResolvedInstall(
            connector_id="mcp",
            kind=ConnectorKind.MCP,
            auth_config_id=uuid4(),
            organization_id=uuid4(),
            config=connection_config,
            config_source=AuthConfigSource.SYSTEM_DEFAULT,
            spec=McpKindSpec(),
        )

        discovered = await dispatcher.discover(install)
        add = next(op for op in discovered if op.name == "add")

        request = dispatcher.build_request(
            connector_id="mcp",
            kind=ConnectorKind.MCP,
            operation=ResolvedOperation(name=add.name, execution=add.execution),
            payload={"a": 20, "b": 22},
            credentials={},
            config=connection_config,
        )
        assert await dispatcher.execute(request) == {"result": 42}
