"""What one MCP operation costs on the wire.

A session lasts one call, so anything per-session is paid per call. These pin
the two things that keep that cost down: the stored output schema standing in
for a ``tools/list``, and the HTTP connections being shared while the
credentials on them are not.
"""

from __future__ import annotations

import asyncio

import httpx2
import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware

from app.modules.connectors.infrastructure.adapters import mcp_executor
from app.modules.connectors.infrastructure.adapters.mcp_executor import (
    McpExecutor,
    StoredToolSchemas,
)

CONN = {"server_url": "https://mcp.round-trips.example/mcp"}


class _CountListings(Middleware):
    def __init__(self) -> None:
        self.listings = 0

    async def on_list_tools(self, context, call_next):
        self.listings += 1
        return await call_next(context)


def _server() -> tuple[FastMCP, _CountListings]:
    counter = _CountListings()
    mcp = FastMCP("round-trips", middleware=[counter])

    @mcp.tool
    def add(a: int, b: int) -> dict:
        return {"sum": a + b}

    @mcp.tool
    def evens(limit: int) -> list[int]:
        return [n for n in range(limit) if n % 2 == 0]

    @mcp.tool
    def plain(text: str) -> str:
        return text.upper()

    return mcp, counter


async def _stored_schemas(server: FastMCP, name: str) -> StoredToolSchemas:
    """What discovery would have stored for ``name``."""
    async with Client(server) as client:
        tool = next(t for t in await client.list_tools() if t.name == name)
    return StoredToolSchemas(
        input_schema=tool.input_schema, output_schema=tool.output_schema
    )


async def _execute(server, name, payload, schemas, connection_config=CONN):
    executor = McpExecutor(client_factory=lambda *_a, **_k: Client(server))
    return await executor.execute(
        connector_id="mcp",
        operation_name=name,
        execution={"kind": "mcp", "tool_name": name},
        payload=payload,
        third_party_credentials=None,
        connection_config=connection_config,
        tool_schemas=schemas,
    )


class TestTheStoredSchemaReplacesTheListing:
    @pytest.mark.asyncio
    async def test_without_a_stored_schema_the_sdk_lists_the_tools(self):
        # The premise: if this stops listing, the seed below is dead weight.
        server, counter = _server()
        result = await _execute(server, "add", {"a": 2, "b": 3}, None)
        assert result == {"sum": 5}
        assert counter.listings == 1

    @pytest.mark.asyncio
    async def test_with_the_stored_schema_the_call_does_not_list(self):
        # Fails if an SDK upgrade renames or stops reading the cache this seeds.
        server, counter = _server()
        schemas = await _stored_schemas(server, "add")
        counter.listings = 0
        result = await _execute(server, "add", {"a": 2, "b": 3}, schemas)
        assert result == {"sum": 5}
        assert counter.listings == 0

    @pytest.mark.asyncio
    async def test_a_wrapped_result_is_unwrapped_from_the_stored_schema(self):
        # fastmcp wraps a non-object result as {"result": ...} and reads the
        # schema to unwrap it; the seed has to keep that working.
        server, counter = _server()
        schemas = await _stored_schemas(server, "evens")
        counter.listings = 0
        assert await _execute(server, "evens", {"limit": 5}, schemas) == [0, 2, 4]
        assert counter.listings == 0

    @pytest.mark.asyncio
    async def test_a_tool_stored_without_an_output_schema_does_not_list(self):
        server, counter = _server()
        schemas = StoredToolSchemas(input_schema={"type": "object"}, output_schema=None)
        result = await _execute(server, "add", {"a": 1, "b": 1}, schemas)
        assert result == {"sum": 2}
        assert counter.listings == 0

    @pytest.mark.asyncio
    async def test_an_empty_stored_schema_reads_as_none(self):
        # Seeded as `{}`, the SDK would demand structured content from a tool
        # that declared none.
        server, counter = _server()
        schemas = StoredToolSchemas(input_schema={"type": "object"}, output_schema={})
        assert await _execute(server, "plain", {"text": "hi"}, schemas) == {
            "result": "HI"
        }
        assert counter.listings == 0

    @pytest.mark.asyncio
    async def test_a_tool_with_header_annotations_still_lists(self):
        # The listing is where the SDK learns which arguments to mirror into
        # headers; skipping it would drop them without a trace.
        server, counter = _server()
        schemas = StoredToolSchemas(
            input_schema={
                "type": "object",
                "properties": {"a": {"type": "integer", "x-mcp-header": "A"}},
            },
            output_schema=None,
        )
        await _execute(server, "add", {"a": 1, "b": 1}, schemas)
        assert counter.listings == 1

    @pytest.mark.asyncio
    async def test_a_client_without_the_cache_falls_back_quietly(self):
        class _Bare:
            session = object()

        mcp_executor._seed_tool_schema(
            _Bare(), "add", StoredToolSchemas({"type": "object"}, None)
        )


class _Recorder(httpx2.AsyncBaseTransport):
    def __init__(self) -> None:
        self.authorizations: list[str | None] = []
        self.closed = False

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.authorizations.append(request.headers.get("authorization"))
        return httpx2.Response(200, json={})

    async def aclose(self) -> None:
        self.closed = True


class TestConnectionsArePooledCredentialsAreNot:
    @pytest.mark.asyncio
    async def test_clients_on_one_loop_share_one_pool(self):
        first = mcp_executor._pooled_http_client()
        second = mcp_executor._pooled_http_client()
        async with first, second:
            assert first._transport._pool is second._transport._pool

    @pytest.mark.asyncio
    async def test_closing_a_client_leaves_the_pool_open_and_headers_stay_per_call(
        self, monkeypatch
    ):
        recorder = _Recorder()
        monkeypatch.setitem(mcp_executor._POOLS, asyncio.get_running_loop(), recorder)
        tenant_a = mcp_executor._pooled_http_client(
            headers={"Authorization": "Bearer a"}
        )
        async with tenant_a:
            await tenant_a.post("https://mcp.round-trips.example/mcp")
        tenant_b = mcp_executor._pooled_http_client(
            headers={"Authorization": "Bearer b"}
        )
        async with tenant_b:
            await tenant_b.post("https://mcp.round-trips.example/mcp")
        unauthenticated = mcp_executor._pooled_http_client()
        async with unauthenticated:
            await unauthenticated.post("https://mcp.round-trips.example/mcp")

        assert recorder.authorizations == ["Bearer a", "Bearer b", None]
        assert not recorder.closed

    def test_the_default_factory_builds_its_client_on_the_pool(self):
        client = mcp_executor.default_mcp_client_factory(
            "https://mcp.round-trips.example/mcp", {"Authorization": "Bearer t"}
        )
        assert client.transport.httpx_client_factory is mcp_executor._pooled_http_client
