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


def _factory(server_url, headers, timeout=None):
    return Client(_SERVER)


# A public https URL, not `memory://`: the executor guards `server_url` before
# connecting, and the real factory only ever builds an HTTP transport, so a
# scheme no deployment can produce is not worth keeping here. `_factory` ignores
# the URL and hands back an in-memory client, so nothing is dialled either way.
CONN = {"server_url": "https://mcp.scenarios.example/mcp"}


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


class TestSessionSetupIsReplayedOnEverySession:
    """A session here lasts exactly one call, so setup has to be replayed.

    The client is built, used and closed per operation, which means any
    server-side state a tool sets up is gone before the next call sees it.
    Servers that gate their tools behind a session-scoped switch -- Arize
    Phoenix's `enable_tool_group` -- were therefore unusable: the gated tools
    were absent from `list_tools()` on a virgin session, so they were never
    discovered, never stored as operations, and never addressable by name.
    """

    @staticmethod
    def _gated_server():
        """A server whose `hidden` tool only appears once setup has run."""
        server = FastMCP("gated-server")

        @server.tool
        def enable_group(group: str) -> dict:
            """Reveal a group of tools."""
            server.enable(names={"hidden"})
            return {"enabled": group}

        @server.tool
        def hidden() -> dict:
            """Only reachable after `enable_group`."""
            return {"secret": 42}

        server.disable(names={"hidden"})
        return server

    @staticmethod
    def _conn(server_url: str = "https://mcp.scenarios.example/mcp", **extra):
        return {"server_url": server_url, **extra}

    SETUP = [{"tool_name": "enable_group", "arguments": {"group": "traces"}}]

    @pytest.mark.asyncio
    async def test_without_setup_a_gated_tool_is_not_discovered(self):
        server = self._gated_server()
        ops = await discover_mcp(
            connection_config=self._conn(),
            client_factory=lambda *a, **k: Client(server),
        )
        assert "hidden" not in {op.name for op in ops}

    @pytest.mark.asyncio
    async def test_with_setup_a_gated_tool_becomes_an_operation(self):
        server = self._gated_server()
        ops = await discover_mcp(
            connection_config=self._conn(session_setup=self.SETUP),
            client_factory=lambda *a, **k: Client(server),
        )
        by_name = {op.name: op for op in ops}
        assert "hidden" in by_name
        assert by_name["hidden"].execution == {"kind": "mcp", "tool_name": "hidden"}

    @pytest.mark.asyncio
    async def test_with_setup_a_gated_tool_can_be_executed(self):
        server = self._gated_server()
        result = await McpExecutor(
            client_factory=lambda *a, **k: Client(server)
        ).execute(
            connector_id="mcp",
            operation_name="hidden",
            execution={"kind": "mcp", "tool_name": "hidden"},
            payload={},
            third_party_credentials=None,
            connection_config=self._conn(session_setup=self.SETUP),
        )
        assert result == {"secret": 42}

    @pytest.mark.asyncio
    async def test_a_malformed_setup_entry_is_skipped_rather_than_fatal(self):
        """The config is tenant-written JSON. An entry that is not a usable
        call must not take every operation on the install down with it."""
        result = await McpExecutor(client_factory=_factory).execute(
            connector_id="mcp",
            operation_name="add",
            execution={"kind": "mcp", "tool_name": "add"},
            payload={"a": 1, "b": 1},
            third_party_credentials=None,
            connection_config=self._conn(
                session_setup=[
                    "not-an-object",
                    {},
                    {"tool_name": ""},
                    {"arguments": {}},
                ]
            ),
        )
        assert result == {"sum": 2}

    def test_setup_steps_are_read_defensively(self):
        from app.modules.connectors.infrastructure.adapters.mcp_executor import (
            _session_setup_steps,
        )

        assert _session_setup_steps(None) == []
        assert _session_setup_steps({"session_setup": "enable_everything"}) == []
        assert _session_setup_steps(
            {"session_setup": [{"tool_name": "go", "arguments": "not-a-map"}]}
        ) == [("go", {})]


class TestTransportFailureClassification:
    """fastmcp buries the real cause; these pin down how we dig it out.

    Getting this wrong is not cosmetic: an unrecognised failure escapes as an
    unhandled 500 instead of a clean domain error, which is what happened for
    every connection refusal before the real-server e2e caught it.
    """

    def test_a_direct_transport_error_is_recognised(self):
        import httpx

        from app.modules.connectors.infrastructure.adapters.mcp_executor import (
            _is_transport_failure,
        )

        assert _is_transport_failure(httpx.ConnectError("refused")) is True

    def test_an_exception_group_of_transport_errors_is_recognised(self):
        import httpx

        from app.modules.connectors.infrastructure.adapters.mcp_executor import (
            _is_transport_failure,
        )

        # anyio task groups wrap whatever the transport raised.
        group = ExceptionGroup("tg", [httpx.ConnectError("refused")])
        assert _is_transport_failure(group) is True

    def test_a_runtime_error_caused_by_a_transport_error_is_recognised(self):
        import httpx

        from app.modules.connectors.infrastructure.adapters.mcp_executor import (
            _is_transport_failure,
        )

        # fastmcp re-raises connect failures as a bare RuntimeError.
        try:
            try:
                raise httpx.ConnectError("refused")
            except httpx.ConnectError as cause:
                raise RuntimeError("Client failed to connect") from cause
        except RuntimeError as exc:
            assert _is_transport_failure(exc) is True

    def test_our_own_bugs_are_not_reported_as_upstream_faults(self):
        from app.modules.connectors.infrastructure.adapters.mcp_executor import (
            _is_transport_failure,
        )

        # A bare RuntimeError with nothing underneath is a defect in this
        # process; misreporting it as an upstream failure would send us hunting
        # the wrong system.
        assert _is_transport_failure(RuntimeError("bad state")) is False
        assert _is_transport_failure(KeyError("missing")) is False

    def test_a_mixed_group_is_not_swallowed(self):
        import httpx

        from app.modules.connectors.infrastructure.adapters.mcp_executor import (
            _is_transport_failure,
        )

        group = ExceptionGroup("tg", [httpx.ConnectError("refused"), KeyError("bug")])
        assert _is_transport_failure(group) is False

    def test_a_self_referential_cause_chain_terminates(self):
        from app.modules.connectors.infrastructure.adapters.mcp_executor import (
            _is_transport_failure,
        )

        # Defensive: a cycle in __context__ must not spin forever.
        first = RuntimeError("a")
        second = RuntimeError("b")
        first.__context__ = second
        second.__context__ = first
        assert _is_transport_failure(first) is False


class TestTheServerUrlIsGuarded:
    """The MCP target is re-checked when a tool is called, not only at install.

    `server_url` is stored and tenant-supplied. Vetting it once, when the
    install was created, leaves the window every other kind closes: DNS can be
    repointed afterwards, and the address that was public then need not be
    public when an agent calls a tool.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "server_url, reason",
        [
            ("https://169.254.169.254/mcp", "link_local_address"),
            ("https://127.0.0.1/mcp", "loopback_address"),
            ("https://10.0.0.5/mcp", "private_address"),
        ],
    )
    async def test_a_private_target_is_refused_at_execution(self, server_url, reason):
        called = False

        def _never(url, headers, timeout=None):
            nonlocal called
            called = True
            return Client(_SERVER)

        executor = McpExecutor(client_factory=_never)
        with pytest.raises(OperationExecutionValidationError) as raised:
            await executor.execute(
                connector_id="mcp",
                operation_name="anything",
                execution={"tool_name": "anything"},
                payload={},
                third_party_credentials=None,
                connection_config={"server_url": server_url},
            )
        assert raised.value.details["reason"] == reason
        # The guard runs before the client is built, so nothing was dialled.
        assert not called

    @pytest.mark.asyncio
    async def test_discovery_refuses_a_private_target_too(self):
        with pytest.raises(ValueError, match="unsafe MCP target"):
            await discover_mcp(
                connection_config={"server_url": "https://169.254.169.254/mcp"},
                client_factory=lambda *a, **k: Client(_SERVER),
            )
