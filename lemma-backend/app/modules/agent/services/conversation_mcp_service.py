"""Request-scoped tool resolution for conversation MCP calls.

The caller is the Agent Host link: a local agent's Lemma tool calls arrive as
``mcp`` frames (``agent_host_link_mcp``), each re-authorized here against the
run's token and its conversation.

This service owns the run token's authorization and the run's session; the
tools are the run's catalog (``tool_catalog``), listed and called through the
shared MCP adapter (``mcp/toolspec_adapter``).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from mcp.types import CallToolResult, Tool

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.toolsets import AgentGrantSummary
from app.modules.agent.domain.value_objects import (
    TERMINAL_AGENT_RUN_STATUSES,
    JsonObject,
    to_json_value,
)
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.infrastructure.mcp import normalize_local_mcp_tool_name
from app.modules.agent.infrastructure.mcp.toolspec_adapter import (
    call_mcp_tool,
    mcp_tools,
)
from app.modules.agent.services.conversation_mcp_authorization import (
    RelayingHost,
    authorize_run_token,
)
from app.modules.agent.services.mcp_pausing_calls import (
    close_pausing_tool_call,
    record_pausing_tool_call,
)
from app.modules.agent.services.run_session_relay import (
    RunInputsCache,
    load_relayed_session,
    recorded_native_commands_for,
    run_inputs_cache,
)
from app.modules.agent.services.tool_catalog import ToolSurface, specs_for
from app.modules.agent.services.tool_runtime import ToolRuntime

logger = get_logger(__name__)


class ConversationMCPService:
    def __init__(self, inputs_cache: RunInputsCache | None = None) -> None:
        self.uow_factory = SessionUnitOfWorkFactory(async_session_maker)
        self._explicit_inputs_cache = inputs_cache

    @property
    def _inputs_cache(self) -> RunInputsCache:
        # Resolved on use, not at construction: the module-level instance
        # below is built at import, before a test or a process has its settings.
        return self._explicit_inputs_cache or run_inputs_cache()

    async def authorize(
        self,
        *,
        conversation_id: UUID,
        token: str,
        agent_run_id: UUID | None = None,
        host: RelayingHost | None = None,
        allow_ended_run: bool = False,
    ) -> bool:
        """Whether ``token`` may use ``conversation_id``'s tools as ``agent_run_id``.

        See ``conversation_mcp_authorization`` for every rule it applies.
        """
        return await authorize_run_token(
            self.uow_factory,
            conversation_id=conversation_id,
            token=token,
            agent_run_id=agent_run_id,
            host=host,
            allow_ended_run=allow_ended_run,
        )

    async def run_has_ended(self, *, agent_run_id: UUID) -> bool:
        """Whether the run is terminal (or gone): nothing will resume it."""
        async with self.uow_factory() as uow:
            run = await Runs(uow).get_agent_run(agent_run_id)
        return run is None or run.status in TERMINAL_AGENT_RUN_STATUSES

    async def parked_tool_return(
        self,
        *,
        conversation_id: UUID,
        tool_call_id: str,
    ) -> JsonObject | None:
        """The answer to a parked interaction, or ``None`` while it is pending.

        The host's MCP bridge waits on this, with an ``interaction_wait`` frame,
        after `ask_user` or `request_approval` hands it a parked id, and hands
        the result back as that tool's return so the model never leaves its
        turn.

        Nothing new is stored to make this work. Deciding an interaction already
        writes a synthesized tool RETURN under the same durable id -- that is how
        the in-process resume replays the answer, and how re-running an approved
        tool is prevented -- so the pending/decided question is just "has that
        return been written yet".
        """
        async with self.uow_factory() as uow:
            message = await Pauses(uow).tool_return(
                conversation_id=conversation_id,
                tool_call_id=tool_call_id,
            )
        if message is None:
            return None
        result = getattr(message, "tool_result", None)
        return result if isinstance(result, dict) else {"result": to_json_value(result)}

    async def list_tools(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID | None = None,
    ) -> list[Tool]:
        runtime, ctx = await self._run_tools(
            conversation_id=conversation_id, agent_run_id=agent_run_id
        )
        return await mcp_tools(
            runtime,
            ctx,
            meta={"agent_run_id": str(agent_run_id)} if agent_run_id else None,
        )

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
        runtime, ctx = await self._run_tools(
            conversation_id=conversation_id, agent_run_id=agent_run_id
        )
        tool_name = normalize_local_mcp_tool_name(name)
        spec = runtime.get(tool_name)
        # A pausing call outlives its own return, so it needs an id the record
        # is addressed by, and MCP supplies none. See ``mcp_pausing_calls``.
        tool_call_id = (
            await record_pausing_tool_call(
                self.uow_factory,
                conversation_id=conversation_id,
                agent_run_id=agent_run_id,
                tool_name=spec.name,
                pauses=spec.policy.pauses,
                arguments=arguments,
            )
            if spec is not None and spec.policy.pauses is not None
            else None
        )
        call = await call_mcp_tool(
            runtime, tool_name, arguments, ctx, tool_call_id=tool_call_id
        )
        if tool_call_id is not None:
            # Answered instead of waiting: finish the recorded call.
            await close_pausing_tool_call(
                self.uow_factory,
                conversation_id=conversation_id,
                agent_run_id=agent_run_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                result=call.payload,
            )
        return call.result

    async def _run_tools(
        self, *, conversation_id: UUID, agent_run_id: UUID | None
    ) -> tuple[ToolRuntime, ToolContext]:
        """The run's tools, exactly as its run spec named them."""
        agent, conversation, ctx, grants = await self._load_agent_context(
            conversation_id=conversation_id, agent_run_id=agent_run_id
        )
        specs = await specs_for(
            agent=agent,
            conversation=conversation,
            ctx=ctx,
            surface=ToolSurface.AGENT_HOST,
            uow_factory=self.uow_factory,
            grants=grants,
        )
        return ToolRuntime(specs), ctx

    async def _load_agent_context(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID | None,
    ) -> tuple[Agent | None, Conversation, ToolContext, AgentGrantSummary | None]:
        """The run's session for this call, and the context its tools see."""
        session = await load_relayed_session(
            self.uow_factory,
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
            inputs_cache=self._inputs_cache,
        )
        ctx = session.relayed_context(agent_run_id=agent_run_id)
        ctx.host_runs_native_commands = await recorded_native_commands_for(
            session.conversation, session.run
        )
        return session.agent, session.conversation, ctx, session.grants


conversation_mcp_service = ConversationMCPService()
