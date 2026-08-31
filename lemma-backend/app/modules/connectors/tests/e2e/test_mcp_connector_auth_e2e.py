"""Whether a token actually reaches an MCP server, against one that demands it.

The other MCP e2e proves we speak the protocol, but it talks to a server that
accepts anyone: every call there passes `third_party_credentials=None`. So the
half of the connector that decides *which* secret to present, and where it comes
from, was never exercised against a server in a position to object.

An MCP install can be authenticated three ways, and they are not
interchangeable: a per-account OAuth token, a per-account bearer token, and a
static token belonging to the install itself (the tenant-supplied API key). They
resolve in that order into one `Authorization` header, and the order is the
whole point -- an account's own credential must win over the install default, or
every user of a shared install silently borrows the same identity.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from typing import Any

import pytest
import pytest_asyncio

from app.modules.connectors.domain.errors import (
    OperationExecutionInfrastructureError,
)
from app.modules.connectors.infrastructure.adapters.mcp_executor import (
    McpExecutor,
    build_mcp_headers,
)
from app.modules.connectors.services.discovery.mcp_discoverer import discover_mcp
from app.modules.test_support.e2e.waiters import eventually

# Set before settings is read anywhere, matching the sibling MCP e2e.
os.environ.setdefault("CONNECTOR_ALLOW_PRIVATE_NETWORK_TARGETS", "true")

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_GOOD_TOKEN = "s3cr3t-token"


@pytest.fixture(autouse=True)
def _reachable_local_server(monkeypatch):
    """Loopback stands in for a self-hosted server; production refuses it.

    Scoped to this file rather than the suite, so the tests that assert the
    guard *refuses* a private target keep working.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "connector_allow_private_network_targets", True)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _authenticated_app():
    """A real MCP server that 401s anything without the right bearer token."""
    from fastmcp import FastMCP

    server = FastMCP("lemma-e2e-auth")

    @server.tool
    def whoami() -> str:
        """Return something only an authorized caller can see."""
        return "authorized"

    inner = server.http_app()

    async def app(scope, receive, send):
        if scope["type"] != "http":
            await inner(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        if headers.get("authorization") != f"Bearer {_GOOD_TOKEN}":
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"unauthorized"})
            return
        await inner(scope, receive, send)

    # Starlette lifespan drives the MCP session manager; without it the inner
    # app refuses every request with "Task group is not initialized".
    app.lifespan = inner.lifespan  # type: ignore[attr-defined]
    return app, inner


@pytest_asyncio.fixture(scope="module")
async def mcp_url():
    import uvicorn

    wrapper, inner = _authenticated_app()
    port = _free_port()

    async def serve():
        async with inner.router.lifespan_context(inner):
            config = uvicorn.Config(
                wrapper, host="127.0.0.1", port=port, log_level="warning"
            )
            await uvicorn.Server(config).serve()

    task = asyncio.create_task(serve())

    async def probe() -> None:
        if task.done():
            raise RuntimeError(f"server failed to start: {task.exception()}")
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        await writer.wait_closed()

    await eventually(
        label=f"authenticated MCP server on {port}",
        probe=probe,
        done=lambda _: True,
        retry_exceptions=(OSError,),
        timeout_seconds=10.0,
        interval_seconds=0.05,
    )
    yield f"http://127.0.0.1:{port}/mcp/"
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _call(config: dict[str, Any], credentials: dict[str, Any] | None):
    return await McpExecutor().execute(
        connector_id="mcp",
        operation_name="whoami",
        execution={"kind": "mcp", "tool_name": "whoami"},
        payload={},
        third_party_credentials=credentials,
        connection_config=config,
    )


class TestTheHeaderIsBuiltFromTheRightPlace:
    """Pure resolution, so the precedence is stated once and cheaply."""

    def test_an_accounts_oauth_token_is_used(self):
        headers = build_mcp_headers({}, {"access_token": "oauth"})
        assert headers["Authorization"] == "Bearer oauth"

    def test_an_accounts_bearer_token_is_used(self):
        headers = build_mcp_headers({}, {"bearer_token": "acct"})
        assert headers["Authorization"] == "Bearer acct"

    def test_the_installs_static_token_is_used_when_the_account_has_none(self):
        headers = build_mcp_headers({"bearer_token": "install"}, {})
        assert headers["Authorization"] == "Bearer install"

    def test_the_account_wins_over_the_install(self):
        """Otherwise every user of a shared install borrows one identity."""
        headers = build_mcp_headers(
            {"bearer_token": "install"}, {"access_token": "mine"}
        )
        assert headers["Authorization"] == "Bearer mine"

    def test_an_explicit_extra_header_is_not_overwritten(self):
        headers = build_mcp_headers(
            {"extra_headers": {"Authorization": "Basic abc"}}, {"access_token": "t"}
        )
        assert headers["Authorization"] == "Basic abc"

    def test_no_token_anywhere_sends_no_authorization(self):
        assert "Authorization" not in build_mcp_headers({}, {})


class TestAgainstAServerThatChecks:
    async def test_an_oauth_account_token_reaches_the_server(self, mcp_url):
        result = await _call({"server_url": mcp_url}, {"access_token": _GOOD_TOKEN})
        assert "authorized" in str(result)

    async def test_the_installs_own_api_key_reaches_the_server(self, mcp_url):
        result = await _call({"server_url": mcp_url, "bearer_token": _GOOD_TOKEN}, None)
        assert "authorized" in str(result)

    async def test_no_credential_is_refused_rather_than_hanging(self, mcp_url):
        with pytest.raises(OperationExecutionInfrastructureError):
            await _call({"server_url": mcp_url}, None)

    async def test_a_wrong_token_is_refused(self, mcp_url):
        with pytest.raises(OperationExecutionInfrastructureError):
            await _call({"server_url": mcp_url}, {"access_token": "nope"})

    async def test_discovery_needs_the_credential_too(self, mcp_url):
        """Discovery is a separate client build; a token that works for
        execution has to reach it as well or an install authenticates and then
        finds nothing."""
        found = await discover_mcp(
            connection_config={"server_url": mcp_url},
            credentials={"access_token": _GOOD_TOKEN},
            timeout_seconds=10.0,
        )
        assert [item.name for item in found] == ["whoami"]
