from __future__ import annotations

import re
from contextlib import asynccontextmanager
from uuid import UUID

import mcp.types
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, AuthProvider
from fastmcp.server.dependencies import get_http_headers
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.modules.agent.infrastructure.mcp import LEMMA_MCP_SERVER_NAME
from app.modules.agent.services.conversation_mcp_service import (
    conversation_mcp_service,
)
from app.modules.agent.services.pod_mcp_service import pod_mcp_service

_CONVERSATION_MCP_PATH = re.compile(
    r"^(?:/agent-runtime/conversations)?/(?P<conversation_id>[0-9a-fA-F-]{36})/mcp/?$"
)
_POD_MCP_PATH = re.compile(
    r"^(?:/agent-runtime/pods)?/(?P<pod_id>[0-9a-fA-F-]{36})/mcp/?$"
)


class LemmaMCPAuthProvider(AuthProvider):
    async def verify_token(self, token: str) -> AccessToken | None:
        if not token:
            return None
        return AccessToken(
            token=token,
            client_id="lemma-daemon",
            subject="conversation-mcp",
            scopes=[],
        )


class ConversationFastMCP(FastMCP):
    async def _list_tools_mcp(
        self,
        request: mcp.types.ListToolsRequest,
    ) -> mcp.types.ListToolsResult:
        del request
        conversation_id, token, agent_run_id = await _request_context()
        await _ensure_authorized(conversation_id=conversation_id, token=token)
        tools = await conversation_mcp_service.list_tools(
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
        )
        return mcp.types.ListToolsResult(tools=tools)

    async def _call_tool_mcp(
        self,
        key: str,
        arguments: dict,
    ) -> mcp.types.CallToolResult:
        conversation_id, token, agent_run_id = await _request_context()
        await _ensure_authorized(conversation_id=conversation_id, token=token)
        return await conversation_mcp_service.call_tool(
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
            name=key,
            arguments=arguments,
        )


async def _request_context() -> tuple[UUID, str, UUID | None]:
    headers = get_http_headers(
        include={
            "authorization",
            "x-lemma-conversation-id",
            "x-lemma-agent-run-id",
        }
    )
    raw_conversation_id = headers.get("x-lemma-conversation-id")
    if not raw_conversation_id:
        raise ValueError("Missing MCP conversation id")
    conversation_id = UUID(raw_conversation_id)
    scheme, _, token = headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ValueError("Missing MCP bearer token")
    raw_agent_run_id = headers.get("x-lemma-agent-run-id")
    agent_run_id = UUID(raw_agent_run_id) if raw_agent_run_id else None
    return conversation_id, token, agent_run_id


async def _ensure_authorized(*, conversation_id: UUID, token: str) -> None:
    if not await conversation_mcp_service.authorize(
        conversation_id=conversation_id,
        token=token,
    ):
        raise ValueError("Unauthorized MCP token")


class ConversationMCPASGIApp:
    def __init__(self) -> None:
        mcp_server = ConversationFastMCP(
            LEMMA_MCP_SERVER_NAME,
            instructions="Lemma tools for the current conversation.",
            auth=LemmaMCPAuthProvider(),
        )
        # stateless_http=True: every request carries the conversation id (URL),
        # bearer token, and agent-run-id headers and re-authorizes per call, so
        # there is no per-session server state to keep. A stateful transport
        # (stateless_http=False) holds the Mcp-Session-Id session in the memory
        # of whichever process handled `initialize`; when the follow-up
        # `notifications/initialized` lands on a different worker/replica (no
        # session affinity) the server returns 404 "session expired" and clients
        # like Codex's rmcp abort the handshake. Stateless avoids that entirely.
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
        match = _CONVERSATION_MCP_PATH.match(path)
        if match is None:
            response = JSONResponse({"error": "not_found"}, status_code=404)
            await response(scope, receive, send)
            return

        headers = list(scope.get("headers") or [])
        headers.append(
            (
                b"x-lemma-conversation-id",
                match.group("conversation_id").encode("ascii"),
            )
        )
        scope = dict(scope)
        scope["path"] = "/mcp"
        scope["raw_path"] = b"/mcp"
        scope["headers"] = headers
        await self._mcp_app(scope, receive, send)


def get_agent_mcp_app() -> ConversationMCPASGIApp:
    return ConversationMCPASGIApp()


class PodFastMCP(FastMCP):
    async def _list_tools_mcp(
        self,
        request: mcp.types.ListToolsRequest,
    ) -> mcp.types.ListToolsResult:
        del request
        pod_id, token = await _pod_request_context()
        if not await pod_mcp_service.authorize(pod_id=pod_id, token=token):
            raise ValueError("Unauthorized pod MCP token")
        tools = await pod_mcp_service.list_tools(pod_id=pod_id, token=token)
        return mcp.types.ListToolsResult(tools=tools)

    async def _call_tool_mcp(
        self,
        key: str,
        arguments: dict,
    ) -> mcp.types.CallToolResult:
        pod_id, token = await _pod_request_context()
        if not await pod_mcp_service.authorize(pod_id=pod_id, token=token):
            raise ValueError("Unauthorized pod MCP token")
        return await pod_mcp_service.call_tool(
            pod_id=pod_id,
            token=token,
            name=key,
            arguments=arguments,
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
        # stateless_http=True: see ConversationMCPASGIApp. Pod tool calls also
        # carry pod id (URL) + bearer token per request and re-authorize, so no
        # per-session state is needed and the stateful-session 404 is avoided.
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
