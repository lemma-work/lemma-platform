"""Request-scoped tool resolution for conversation MCP calls.

Thin adapter over `AgentToolDispatcher`: this service owns the conversation
authorization + context loading and the MCP wire format (``lemma_``-prefixed
names, `CallToolResult` wrapping); the dispatcher owns toolset resolution and
the actual tool invocation.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from mcp.types import CallToolResult, TextContent, Tool
from supertokens_python.recipe.session.asyncio import (
    get_session_without_request_response,
)

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import JsonObject, to_json_value
from app.modules.agent.infrastructure.mcp import (
    exported_tool_name,
    normalize_local_mcp_tool_name,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.tools.callable_tool_factory import inline_tool_schema_refs
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.dispatcher import AgentToolDispatcher
from app.modules.agent.tools.tool_errors import (
    format_tool_error,
    is_control_flow_exception,
)

logger = get_logger(__name__)


class ConversationMCPService:
    def __init__(self) -> None:
        self.uow_factory = SessionUnitOfWorkFactory(async_session_maker)
        self.dispatcher = AgentToolDispatcher(self.uow_factory)

    async def authorize(self, *, conversation_id: UUID, token: str) -> bool:
        try:
            session = await get_session_without_request_response(
                token,
                anti_csrf_check=False,
                session_required=True,
            )
        except Exception:
            return False
        if session is None:
            return False
        token_user_id = UUID(session.get_user_id())
        async with self.uow_factory() as uow:
            conversation = await ConversationRepository(uow).get_conversation(
                conversation_id,
                include_runs=False,
            )
        return conversation is not None and conversation.user_id == token_user_id

    async def list_tools(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID | None = None,
    ) -> list[Tool]:
        agent, conversation, ctx = await self._load_agent_context(
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
        )
        tools = await self.dispatcher.list_tools(
            agent=agent,
            conversation=conversation,
            ctx=ctx,
            agent_run_id=agent_run_id,
        )
        return [
            Tool(
                name=exported_tool_name(tool.name),
                description=tool.description,
                inputSchema=inline_tool_schema_refs(tool.input_schema),
                _meta={
                    "lemma_tool_name": tool.name,
                    **(
                        {"agent_run_id": str(agent_run_id)}
                        if agent_run_id is not None
                        else {}
                    ),
                },
            )
            for tool in tools
        ]

    async def exported_tool_names(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID | None = None,
    ) -> list[str]:
        return [
            tool.name
            for tool in await self.list_tools(
                conversation_id=conversation_id,
                agent_run_id=agent_run_id,
            )
        ]

    async def call_tool(
        self,
        *,
        conversation_id: UUID,
        name: str,
        arguments: dict[str, Any] | None,
        agent_run_id: UUID | None = None,
    ) -> CallToolResult:
        agent, conversation, ctx = await self._load_agent_context(
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
        )
        tool_name = normalize_local_mcp_tool_name(name)
        try:
            result = await self.dispatcher.call_tool(
                agent=agent,
                conversation=conversation,
                ctx=ctx,
                name=tool_name,
                arguments=arguments,
                agent_run_id=agent_run_id,
            )
        except Exception as exc:  # noqa: BLE001 - graceful tool-error boundary
            if is_control_flow_exception(exc):
                raise
            # Return the failure as an MCP tool error (isError) so the daemon's
            # model recovers and continues the turn, instead of the unknown-tool /
            # validation / execution exception surfacing as a protocol/HTTP error
            # that aborts the run.
            logger.warning(
                "Conversation MCP tool %r failed; returning isError result: %s",
                tool_name,
                exc,
                exc_info=True,
            )
            return self._mcp_error_result(tool_name, exc)
        return self._mcp_result(result)

    async def _load_agent_context(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID | None,
    ) -> tuple[Agent | None, Conversation, BaseAgentContext]:
        async with self.uow_factory() as uow:
            conversation_repo = ConversationRepository(uow)
            agent_repo = AgentRepository(uow)
            conversation = await conversation_repo.get_conversation(
                conversation_id,
                include_runs=True,
            )
            if conversation is None:
                raise ValueError(f"Conversation {conversation_id} not found")
            run = None
            if agent_run_id is not None:
                run = await conversation_repo.get_agent_run(agent_run_id)
            if run is None:
                run = await conversation_repo.get_active_agent_run(conversation_id)
            agent_id = conversation.agent_id or (run.agent_id if run else None)
            agent = await agent_repo.get(agent_id) if agent_id is not None else None
            ctx = BaseAgentContext(
                user_id=conversation.user_id,
                org_id=conversation.organization_id,
                pod_id=conversation.pod_id,
                conversation_id=conversation.id,
                agent_name=agent.name if agent is not None else None,
                agent_run_id=agent_run_id or (run.id if run is not None else None),
                runtime_profile=(
                    run.agent_runtime.model_dump(mode="json") if run else None
                ),
                **_surface_context_from_conversation(conversation),
            )
            return agent, conversation, ctx

    def _mcp_result(self, result: object) -> CallToolResult:
        payload = to_json_value(result)
        if isinstance(payload, dict):
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(payload, default=str),
                    )
                ],
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


conversation_mcp_service = ConversationMCPService()


def _surface_platform(conversation: Conversation) -> str | None:
    metadata = conversation.metadata or {}
    platform = metadata.get("surface_platform") if isinstance(metadata, dict) else None
    return str(platform) if platform else None


def _surface_context_from_conversation(conversation: Conversation) -> JsonObject:
    metadata = conversation.metadata or {}
    surface_id = metadata.get("surface_id")
    surface_metadata_payload = metadata.get("surface_event_metadata")
    surface_metadata = None
    if isinstance(surface_metadata_payload, dict):
        try:
            from app.modules.agent_surfaces.domain.surface_event_metadata import (
                SurfaceEventMetadata,
            )
            from pydantic import TypeAdapter

            surface_metadata = TypeAdapter(SurfaceEventMetadata).validate_python(
                surface_metadata_payload
            )
        except Exception:
            surface_metadata = surface_metadata_payload
    return {
        "surface_id": UUID(str(surface_id)) if surface_id else None,
        "surface_platform": metadata.get("surface_platform"),
        "surface_metadata": surface_metadata,
        "external_channel_id": metadata.get("external_channel_id"),
        "external_thread_id": metadata.get("external_thread_id"),
        "external_user_id": metadata.get("external_user_id"),
        "external_message_id": metadata.get("external_message_id"),
        "agent_display_name": metadata.get("agent_display_name"),
    }
