from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import mcp.types
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, AuthProvider
from fastmcp.server.context import ServerRequestContext
from fastmcp.server.dependencies import bind_request_context, get_http_headers
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.cors import get_allowed_cors_origin_regex, get_allowed_cors_origins
from app.modules.agent.infrastructure.mcp import LEMMA_MCP_SERVER_NAME
from app.modules.agent.services.pod_mcp_service import pod_mcp_service
from app.modules.mcp_access.contracts import (
    MCP_MOUNT_PATH,
    McpPrincipal,
    bearer_challenge,
    is_mcp_access_token,
    request_retry_after,
    verify_mcp_access_token,
)

_POD_MCP_PATH = re.compile(
    r"^(?:/agent-runtime/pods)?/(?P<pod_id>[0-9a-fA-F-]{36})/mcp/?$"
)
# The public mount, `/mcp/{pod_id}`: the URL a person pastes into Claude or
# ChatGPT, and the resource their token is issued for.
_PUBLIC_POD_MCP_PATH = re.compile(
    rf"^(?:{MCP_MOUNT_PATH})?/(?P<pod_id>[0-9a-fA-F-]{{36}})/?$"
)
# Headers the wrappers below set for FastMCP. Removed from what the client sent
# first, so a request cannot name a different pod than its URL does.
_INTERNAL_HEADERS = (b"x-lemma-pod-id",)


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


def _forward_to_pod(scope: Scope, pod_id: str) -> Scope:
    headers = [
        (name, value)
        for name, value in scope.get("headers") or []
        if name.lower() not in _INTERNAL_HEADERS
    ]
    headers.append((b"x-lemma-pod-id", pod_id.encode("ascii")))
    forwarded = dict(scope)
    forwarded["path"] = "/mcp"
    forwarded["raw_path"] = b"/mcp"
    # Starlette routes on the path *below* root_path. Mounted at `/mcp`, the
    # root path is `/mcp` too, so the rewritten path would route as empty.
    forwarded["root_path"] = ""
    forwarded["headers"] = headers
    return forwarded


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

        # An outside client's token belongs on the public mount, where its
        # rate limit and Origin check are. Refused here rather than served
        # without them.
        _, _, token = Headers(scope=scope).get("authorization", "").partition(" ")
        if is_mcp_access_token(token):
            pod_id = UUID(match.group("pod_id"))
            await _unauthorized(pod_id, error="invalid_token")(scope, receive, send)
            return

        await self._mcp_app(
            _forward_to_pod(scope, match.group("pod_id")), receive, send
        )


def _origin_allowed(origin: str) -> bool:
    if origin in get_allowed_cors_origins():
        return True
    pattern = get_allowed_cors_origin_regex()
    return bool(pattern and re.fullmatch(pattern, origin))


class _VerifiesAccessTokens(Protocol):
    async def __call__(self, token: str, *, pod_id: UUID) -> McpPrincipal | None: ...


class _AuthorizesSessions(Protocol):
    async def __call__(self, *, pod_id: UUID, token: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class PublicMCPGate:
    """What the public mount asks before it lets a request through. Its
    collaborators, named, so a test can stand in for each one."""

    verify_access_token: _VerifiesAccessTokens = verify_mcp_access_token
    authorize_session: _AuthorizesSessions = pod_mcp_service.authorize
    retry_after: Callable[[McpPrincipal], Awaitable[int | None]] = request_retry_after
    origin_allowed: Callable[[str], bool] = _origin_allowed


class PublicPodMCPApp:
    """The pod MCP endpoint for outside clients, at ``/mcp/{pod_id}``.

    The same FastMCP app the Agent Host reaches at ``/agent-runtime/pods``, with
    the HTTP-level answers an OAuth client depends on put in front of it. There
    the token is checked inside the JSON-RPC handler and a bad one becomes a
    JSON-RPC error -- which a client holding an expired token cannot tell from
    any other failure. Here it is a 401 carrying the challenge that says where
    to sign in, which is the only thing that makes Claude start its OAuth flow.

    The Agent Host's mount is left as it was: a 401 there would be new
    behaviour for a client that never needed it, and some MCP clients answer
    a 401 by starting an OAuth discovery the Agent Host has no use for.
    """

    def __init__(
        self, pod_app: PodMCPASGIApp, gate: PublicMCPGate | None = None
    ) -> None:
        self._pod_app = pod_app
        self._gate = gate or PublicMCPGate()

    @property
    def _mcp_app(self) -> ASGIApp:
        return self._pod_app._mcp_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await JSONResponse({"error": "not_found"}, status_code=404)(
                scope, receive, send
            )
            return
        match = _PUBLIC_POD_MCP_PATH.match(str(scope.get("path") or ""))
        if match is None:
            await JSONResponse({"error": "not_found"}, status_code=404)(
                scope, receive, send
            )
            return
        pod_id = UUID(match.group("pod_id"))
        refusal = await self._refusal(Headers(scope=scope), pod_id)
        if refusal is not None:
            await refusal(scope, receive, send)
            return
        await self._mcp_app(_forward_to_pod(scope, str(pod_id)), receive, send)

    async def _refusal(self, headers: Headers, pod_id: UUID) -> JSONResponse | None:
        # MCP streamable HTTP: a server MUST validate Origin. Server-side
        # clients send none; a browser page may use this only from an origin
        # the API already trusts, the same rule CORS applies to the rest of it.
        origin = headers.get("origin")
        if origin and not self._gate.origin_allowed(origin):
            return JSONResponse({"error": "origin_not_allowed"}, status_code=403)

        scheme, _, token = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            return _unauthorized(pod_id, error=None)
        if not is_mcp_access_token(token):
            # A Lemma session, as the Agent Host and the CLI hold. Checked here
            # too, so a stale one is a 401 on this mount like any other.
            if await self._gate.authorize_session(pod_id=pod_id, token=token):
                return None
            return _unauthorized(pod_id, error="invalid_token")
        principal = await self._gate.verify_access_token(token, pod_id=pod_id)
        if principal is None:
            return _unauthorized(pod_id, error="invalid_token")
        wait = await self._gate.retry_after(principal)
        if wait is not None:
            return JSONResponse(
                {"error": "rate_limited", "retry_after_seconds": wait},
                status_code=429,
                headers={"Retry-After": str(wait)},
            )
        return None


def _unauthorized(pod_id: UUID, *, error: str | None) -> JSONResponse:
    return JSONResponse(
        {"error": error or "unauthorized"},
        status_code=401,
        headers={"WWW-Authenticate": bearer_challenge(pod_id, error=error)},
    )


def get_pod_mcp_app() -> PodMCPASGIApp:
    return PodMCPASGIApp()
