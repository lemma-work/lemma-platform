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

import os

import pytest
import pytest_asyncio

from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.domain.connector_operation import ResolvedOperation
from app.modules.connectors.domain.errors import (
    OperationExecutionInfrastructureError,
    OperationExecutionTimeoutError,
    OperationExecutionValidationError,
)
from app.modules.connectors.infrastructure.adapters.mcp_executor import McpExecutor
from app.modules.connectors.infrastructure.kinds import build_kind_registry
from app.modules.connectors.services.discovery.mcp_discoverer import discover_mcp
from app.modules.connectors.services.execution import KindDispatcher
from app.modules.test_support.e2e.waiters import eventually


# Before settings is read anywhere, so the worker and any other reader see it
# too — patching the attribute alone reaches one instance and one moment.
os.environ.setdefault("CONNECTOR_ALLOW_PRIVATE_NETWORK_TARGETS", "true")


@pytest.fixture(autouse=True)
def _reachable_local_server(monkeypatch):
    """These connect to a real server on loopback, so model self-hosting.

    The kind re-checks its target when the call is made now, not only when the
    install was created, and production refuses loopback — correctly. Scoped to
    this file rather than the whole e2e suite on purpose: a blanket fixture
    would also disable the guard for the tests that assert it *refuses*, which
    is how a security check quietly stops being tested.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "connector_allow_private_network_targets", True)


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

    # A group-gated tool, as Arize Phoenix has: absent from `tools/list` until
    # something in the same session turns it on. This is the shape the session
    # preamble exists for, and it can only be proved over the wire -- an
    # in-memory client shares too much with the server to distinguish "the
    # setup call ran first" from "the tool was always there".
    @server.tool
    def enable_tool_group(group: str) -> dict:
        """Reveal a group of tools for this session."""
        server.enable(names={"traces_for"})
        return {"enabled": group}

    @server.tool
    def traces_for(project: str) -> dict:
        """Only listed once `enable_tool_group` has run."""
        return {"project": project, "traces": 3}

    server.disable(names={"traces_for"})

    return server


#: The running server object, so a test can put its gated tool back the way it
#: found it. `FastMCP.enable` is server-wide, not session-scoped, so without
#: this the "invisible until enabled" assertion would pass or fail depending on
#: which test in the class ran first.
_RUNNING_SERVER: list[Any] = []


@pytest_asyncio.fixture(scope="module")
async def mcp_server():
    """A real MCP server on a real port for the duration of the module."""
    server = _build_server()
    _RUNNING_SERVER[:] = [server]
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

    async def test_a_failing_tool_is_the_server_refusing_this_call(
        self, connection_config
    ):
        """Not an outage, and calling it one cost twice over.

        The breaker counts infrastructure errors, so five bad-argument calls
        inside its window disabled a healthy MCP server for a whole
        organization. And `OperationExecutionInfrastructureError` hardcodes its
        own message, so the tool's explanation was replaced by "Connector
        provider is temporarily unavailable" -- which tells an agent nothing it
        can act on and everything it needs to retry.

        This test asserted the wrong classification for as long as the bug
        existed. It runs against a real server, so unlike the unit stand-in it
        did reach the true path; it simply agreed with it.
        """
        with pytest.raises(OperationExecutionValidationError) as caught:
            await _call(connection_config, "explode")

        assert "tool blew up" in caught.value.details["upstream_message"]
        assert caught.value.details["operation_name"] == "explode"

    async def test_calling_a_tool_that_does_not_exist_fails_cleanly(
        self, connection_config
    ):
        """A name the server does not have is a bad request, not a bad server."""
        with pytest.raises(OperationExecutionValidationError) as caught:
            await _call(connection_config, "no_such_tool")

        assert "no_such_tool" in caught.value.details["upstream_message"]

    async def test_an_unreachable_server_fails_rather_than_hanging(self):
        # A closed port on localhost: the connection is refused immediately, so
        # this asserts the error mapping, not the timeout.
        with pytest.raises(OperationExecutionInfrastructureError):
            await _call({"server_url": f"http://127.0.0.1:{_free_port()}/mcp"}, "add")


class TestSessionSetup:
    """A session here lasts exactly one call, so setup has to be replayed.

    Without it a server that gates tools behind a session-scoped switch is
    unusable: the gated tools are absent from `tools/list` on a virgin session,
    so they are never discovered, never stored as operations, and never
    addressable by name -- and calling the switch as its own operation has no
    effect on the next call, because that opens a different session.
    """

    SETUP = [{"tool_name": "enable_tool_group", "arguments": {"group": "traces"}}]

    @pytest.fixture(autouse=True)
    def _gate_closed(self, mcp_server):
        """Start every test from the gate shut.

        `FastMCP.enable` is server-wide where Phoenix's is session-scoped, so
        one test in this class enabling the tool would otherwise decide the
        answer for whichever ran next.
        """
        _RUNNING_SERVER[0].disable(names={"traces_for"})

    async def test_a_gated_tool_is_invisible_without_it(self, connection_config):
        found = await discover_mcp(connection_config=connection_config)

        assert "traces_for" not in {op.name for op in found}

    async def test_the_preamble_makes_a_gated_tool_a_real_operation(
        self, connection_config
    ):
        found = await discover_mcp(
            connection_config={**connection_config, "session_setup": self.SETUP}
        )

        assert "traces_for" in {op.name for op in found}

    async def test_the_preamble_runs_before_the_call_it_precedes(
        self, connection_config
    ):
        result = await _call(
            {**connection_config, "session_setup": self.SETUP},
            "traces_for",
            {"project": "lemma"},
        )

        assert result == {"project": "lemma", "traces": 3}


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
