"""Request-scoped tool resolution for the ``pod/{id}`` MCP surface.

Mirrors `ConversationMCPService` but is scoped to a pod instead of a
conversation: the pod id is injected from the URL and the pod toolset is exposed
with ``lemma_``-prefixed names. The caller's token (a user session, optionally
carrying agent delegation claims) determines the authorization principal; the
pod tools then enforce per-resource grants.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from mcp.types import CallToolResult, TextContent, Tool
from supertokens_python.recipe.session.asyncio import (
    get_session_without_request_response,
)

from app.core.authorization.delegation import (
    WorkloadPrincipalType,
    parse_delegation_claims,
)
from app.core.config import settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import to_json_value
from app.modules.agent.infrastructure.mcp import (
    exported_tool_name,
    normalize_local_mcp_tool_name,
)
from app.modules.agent.tools.callable_tool_factory import inline_tool_schema_refs
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.dispatcher import AgentToolDispatcher
from app.modules.agent.tools.pod.pydantic_adapter import pod_toolset
from app.modules.agent.tools.tool_errors import (
    format_tool_error,
    is_control_flow_exception,
)

logger = get_logger(__name__)

_NIL_CONVERSATION_ID = UUID(int=0)


class PodMCPService:
    def __init__(self) -> None:
        self.uow_factory = SessionUnitOfWorkFactory(async_session_maker)
        self.dispatcher = AgentToolDispatcher(self.uow_factory)

    async def authorize(self, *, pod_id: UUID, token: str) -> bool:
        ctx = await self._context_from_token(pod_id=pod_id, token=token)
        return ctx is not None

    async def list_tools(self, *, pod_id: UUID, token: str) -> list[Tool]:
        ctx = await self._require_context(pod_id=pod_id, token=token)
        tools = await self.dispatcher.list_tools(ctx=ctx, toolsets=[pod_toolset])
        return [
            Tool(
                name=exported_tool_name(tool.name),
                description=tool.description,
                inputSchema=inline_tool_schema_refs(tool.input_schema),
                _meta={"lemma_tool_name": tool.name},
            )
            for tool in tools
        ]

    async def call_tool(
        self,
        *,
        pod_id: UUID,
        token: str,
        name: str,
        arguments: dict[str, Any] | None,
    ) -> CallToolResult:
        ctx = await self._require_context(pod_id=pod_id, token=token)
        tool_name = normalize_local_mcp_tool_name(name)
        try:
            result = await self.dispatcher.call_tool(
                ctx=ctx,
                toolsets=[pod_toolset],
                name=tool_name,
                arguments=arguments,
            )
        except Exception as exc:  # noqa: BLE001 - graceful tool-error boundary
            if is_control_flow_exception(exc):
                raise
            logger.warning(
                "Pod MCP tool %r failed; returning isError result: %s",
                tool_name,
                exc,
                exc_info=True,
            )
            return self._mcp_error_result(tool_name, exc)
        return self._mcp_result(result)

    async def _require_context(self, *, pod_id: UUID, token: str) -> BaseAgentContext:
        ctx = await self._context_from_token(pod_id=pod_id, token=token)
        if ctx is None:
            raise ValueError("Unauthorized pod MCP token")
        return ctx

    async def _context_from_token(
        self,
        *,
        pod_id: UUID,
        token: str,
    ) -> BaseAgentContext | None:
        try:
            session = await get_session_without_request_response(
                token,
                anti_csrf_check=False,
                session_required=True,
            )
        except Exception:
            return None
        if session is None:
            return None
        user_id = UUID(session.get_user_id())

        workload_id: UUID | None = None
        agent_name: str | None = None
        if settings.authz_delegated_tokens_enabled:
            try:
                claims = parse_delegation_claims(
                    session.get_access_token_payload() or {}
                )
            except Exception:
                claims = None
            if claims is not None and claims.actor_type == WorkloadPrincipalType.AGENT:
                if claims.pod_id != pod_id:
                    return None
                workload_id = claims.actor_id
                agent_name = claims.actor_name

        return BaseAgentContext(
            user_id=user_id,
            pod_id=pod_id,
            conversation_id=_NIL_CONVERSATION_ID,
            workload_type="agent" if workload_id is not None else None,
            workload_id=workload_id,
            agent_name=agent_name,
        )

    def _mcp_result(self, result: object) -> CallToolResult:
        payload = to_json_value(result)
        if isinstance(payload, dict):
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(payload, default=str))],
                structuredContent=payload,
            )
        text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
        return CallToolResult(content=[TextContent(type="text", text=text)])

    def _mcp_error_result(self, name: str, exc: Exception) -> CallToolResult:
        payload = format_tool_error(name, exc)
        return CallToolResult(
            isError=True,
            content=[
                TextContent(type="text", text=json.dumps(payload, default=str))
            ],
            structuredContent=payload,
        )


pod_mcp_service = PodMCPService()
