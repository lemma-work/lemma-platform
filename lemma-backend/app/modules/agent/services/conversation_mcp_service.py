"""Request-scoped tool resolution for conversation MCP calls.

The caller is the Agent Host link: a local agent's Lemma tool calls arrive as
``mcp`` frames (``agent_host_link_mcp``), each re-authorized here against the
run's token and its conversation.

Thin adapter over `AgentToolDispatcher`: this service owns the conversation
authorization + context loading and the MCP wire format (``lemma_``-prefixed
names, `CallToolResult` wrapping); the dispatcher owns toolset resolution and
the actual tool invocation.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from mcp.types import CallToolResult, Tool

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.infrastructure.mcp_content import (
    tool_call_error,
    tool_call_result,
)
from app.modules.agent.domain.value_objects import (
    TERMINAL_AGENT_RUN_STATUSES,
    JsonObject,
    to_json_value,
)
from app.modules.agent.infrastructure.mcp import (
    exported_tool_name,
    normalize_local_mcp_tool_name,
)

from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.services.conversation_mcp_authorization import (
    RelayingHost,
    authorize_run_token,
)
from app.modules.agent.services.mcp_pausing_calls import (
    close_pausing_tool_call,
    is_pausing_tool,
    record_pausing_tool_call,
)
from app.modules.agent.infrastructure.tools.callables import inline_tool_schema_refs
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.dispatcher import AgentToolDispatcher
from app.modules.agent.infrastructure.tools.toolset_selection import AgentGrantSummary
from app.modules.agent.services.run_session_relay import (
    RunInputsCache,
    load_relayed_session,
    recorded_native_commands_for,
    run_inputs_cache,
)
from app.modules.agent.infrastructure.tools.tool_errors import (
    is_control_flow_exception,
)

logger = get_logger(__name__)


class ConversationMCPService:
    def __init__(self, inputs_cache: RunInputsCache | None = None) -> None:
        self.uow_factory = SessionUnitOfWorkFactory(async_session_maker)
        self.dispatcher = AgentToolDispatcher(self.uow_factory)
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
            message = await Pauses(uow).get_tool_return(
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
        agent, conversation, ctx, grants = await self._load_agent_context(
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
        )
        tools = await self.dispatcher.list_tools(
            agent=agent,
            conversation=conversation,
            ctx=ctx,
            agent_run_id=agent_run_id,
            grants=grants,
            # This is reached only by the Agent Host MCP bridge, which is
            # the harness that has no other way to return a structured result.
            # The assembler applies the "does this run owe one?" gate.
            include_final_answer=True,
        )
        return [
            Tool(
                name=exported_tool_name(tool.name),
                description=tool.description,
                input_schema=inline_tool_schema_refs(tool.input_schema),
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
        agent, conversation, ctx, grants = await self._load_agent_context(
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
        )
        tool_name = normalize_local_mcp_tool_name(name)
        # A tool that outlives its own return needs an id the record is
        # addressed by, and MCP supplies none. See ``mcp_pausing_calls``.
        tool_call_id = (
            await record_pausing_tool_call(
                self.uow_factory,
                conversation_id=conversation_id,
                agent_run_id=agent_run_id,
                tool_name=tool_name,
                arguments=arguments,
            )
            if is_pausing_tool(tool_name)
            else None
        )
        try:
            result = await self.dispatcher.call_tool(
                agent=agent,
                conversation=conversation,
                ctx=ctx,
                name=tool_name,
                arguments=arguments,
                agent_run_id=agent_run_id,
                grants=grants,
                tool_call_id=tool_call_id,
                # Must match list_tools, or we advertise a tool we then reject
                # as unknown.
                include_final_answer=True,
            )
        except Exception as exc:  # noqa: BLE001 - graceful tool-error boundary
            if is_control_flow_exception(exc):
                raise
            # Return the failure as an MCP tool error (isError) so the harness's
            # model recovers and continues the turn, instead of the unknown-tool /
            # validation / execution exception surfacing as a protocol/HTTP error
            # that aborts the run.
            logger.warning(
                "agent.conversation_mcp_service.conversation_mcp_tool_r_returning.degraded",
                exc_info=True,
            )
            error = tool_call_error(tool_name, exc)
            await self._close_if_it_did_not_wait(
                tool_call_id=tool_call_id,
                conversation_id=conversation_id,
                agent_run_id=agent_run_id,
                tool_name=tool_name,
                result=error.structured_content,
            )
            return error
        await self._close_if_it_did_not_wait(
            tool_call_id=tool_call_id,
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
            tool_name=tool_name,
            result=result,
        )
        return tool_call_result(result)

    async def _close_if_it_did_not_wait(
        self,
        *,
        tool_call_id: str | None,
        conversation_id: UUID,
        agent_run_id: UUID | None,
        tool_name: str,
        result: object,
    ) -> None:
        """Finish a recorded pausing call that answered instead of waiting."""
        if tool_call_id is None:
            return
        await close_pausing_tool_call(
            self.uow_factory,
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            result=result,
        )

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
