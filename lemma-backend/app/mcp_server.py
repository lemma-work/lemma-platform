from __future__ import annotations

import re
from contextlib import asynccontextmanager
from uuid import UUID

import mcp.types
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, AuthProvider
from fastmcp.server.context import ServerRequestContext
from fastmcp.server.dependencies import bind_request_context, get_http_headers
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.modules.agent.infrastructure.mcp import LEMMA_MCP_SERVER_NAME
from app.modules.agent.services.pod_mcp_service import pod_mcp_service

_POD_MCP_PATH = re.compile(
    r"^(?:/agent-runtime/pods)?/(?P<pod_id>[0-9a-fA-F-]{36})/mcp/?$"
)


# These two are the only reason the subclass below exists: Lemma serves tools
# per pod, resolved from request headers, not the static
# set a FastMCP instance registers. They are private names on a dependency, and
# fastmcp 4 renamed them from `_list_tools_mcp`/`_call_tool_mcp`.
#
# A rename is not loud. An override that no longer matches anything is a method
# nobody calls, so the base implementation answers instead -- with the tools
# this server has registered, which is none. Every tool listing silently comes
# back empty and every call is an unknown tool. That is why this is asserted at
# import: refusing to start is the only version of this failure anyone notices.
for _hook in ("_on_list_tools", "_on_call_tool"):
    if not hasattr(FastMCP, _hook):
        raise RuntimeError(
            f"fastmcp.FastMCP has no {_hook!r}; the MCP tool hooks have been "
            "renamed again. Re-point the overrides in app/mcp_server.py at the "
            "new names -- leaving them stale serves an empty tool list."
        )


class LemmaMCPAuthProvider(AuthProvider):
    async def verify_token(self, token: str) -> AccessToken | None:
        if not token:
            return None
        return AccessToken(
            token=token,
            client_id="lemma-agent-host",
            subject="pod-mcp",
            scopes=[],
        )


class PodFastMCP(FastMCP):
    async def _on_list_tools(
        self,
        ctx: ServerRequestContext,
        params: mcp.types.PaginatedRequestParams | None,
    ) -> mcp.types.ListToolsResult:
        del params
        with bind_request_context(ctx):
            pod_id, token = await _pod_request_context()
            if not await pod_mcp_service.authorize(pod_id=pod_id, token=token):
                raise ValueError("Unauthorized pod MCP token")
            tools = await pod_mcp_service.list_tools(pod_id=pod_id, token=token)
        return mcp.types.ListToolsResult(tools=tools)

    async def _on_call_tool(
        self,
        ctx: ServerRequestContext,
        params: mcp.types.CallToolRequestParams,
    ) -> mcp.types.CallToolResult:
        with bind_request_context(ctx):
            pod_id, token = await _pod_request_context()
            if not await pod_mcp_service.authorize(pod_id=pod_id, token=token):
                raise ValueError("Unauthorized pod MCP token")
            return await pod_mcp_service.call_tool(
                pod_id=pod_id,
                token=token,
                name=params.name,
                arguments=params.arguments or {},
            )


async def _pod_request_context() -> tuple[UUID, str]:
    headers = get_http_headers(include={"authorization", "x-lemma-pod-id"})
    raw_pod_id = headers.get("x-lemma-pod-id")
    if not raw_pod_id:
        raise ValueError("Missing MCP pod id")
    pod_id = UUID(raw_pod_id)
    scheme, _, token = headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ValueError("Missing MCP bearer token")
    return pod_id, token


class PodMCPASGIApp:
    def __init__(self) -> None:
        mcp_server = PodFastMCP(
            LEMMA_MCP_SERVER_NAME,
            instructions="Lemma tools for the current pod's datastore.",
            auth=LemmaMCPAuthProvider(),
        )
        # stateless_http=True: every request carries the pod id (URL) and a
        # bearer token and re-authorizes per call, so there is no per-session
        # server state to keep. A stateful transport holds the Mcp-Session-Id
        # session in the memory of whichever process handled `initialize`; when
        # the follow-up `notifications/initialized` lands on a different
        # worker/replica (no session affinity) the server returns 404 "session
        # expired" and clients like Codex's rmcp abort the handshake.
        #
        # The flag only governs the *handshake* era now. MCP revision
        # 2026-07-28 removed protocol sessions and the initialize handshake
        # outright, so a client speaking it is self-describing per request and
        # is routed before this flag is consulted. It stays because FastMCP 4
        # serves both eras at once, and clients on the older handshake
        # revisions are what this keeps working across replicas.
        self._mcp_app = mcp_server.http_app(
            path="/mcp",
            transport="http",
            json_response=True,
            stateless_http=True,
        )

    @asynccontextmanager
    async def lifespan(self, app):
        async with self._mcp_app.lifespan(app):
            yield

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._mcp_app(scope, receive, send)
            return
        if scope["type"] != "http":
            response = JSONResponse({"error": "not_found"}, status_code=404)
            await response(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        match = _POD_MCP_PATH.match(path)
        if match is None:
            response = JSONResponse({"error": "not_found"}, status_code=404)
            await response(scope, receive, send)
            return

        headers = list(scope.get("headers") or [])
        headers.append((b"x-lemma-pod-id", match.group("pod_id").encode("ascii")))
        scope = dict(scope)
        scope["path"] = "/mcp"
        scope["raw_path"] = b"/mcp"
        scope["headers"] = headers
        await self._mcp_app(scope, receive, send)


def get_pod_mcp_app() -> PodMCPASGIApp:
    return PodMCPASGIApp()
