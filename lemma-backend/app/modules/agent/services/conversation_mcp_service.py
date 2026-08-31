"""Request-scoped tool resolution for conversation MCP calls.

Thin adapter over `AgentToolDispatcher`: this service owns the conversation
authorization + context loading and the MCP wire format (``lemma_``-prefixed
names, `CallToolResult` wrapping); the dispatcher owns toolset resolution and
the actual tool invocation.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from mcp.types import CallToolResult, Tool
from supertokens_python.recipe.session.asyncio import (
    get_session_without_request_response,
)
from supertokens_python.recipe.session.exceptions import SuperTokensSessionError

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.services.surface_context import (
    surface_context_from_conversation,
)
from app.modules.agent.domain.entities import Agent, AgentRun, Conversation
from app.modules.agent.domain.vision import vision_mode_from_runtime_profile
from app.modules.agent.services.mcp_content import (
    tool_call_error,
    tool_call_result,
)
from app.modules.agent.domain.value_objects import JsonObject, to_json_value
from app.modules.agent.infrastructure.mcp import (
    exported_tool_name,
    normalize_local_mcp_tool_name,
)
from sqlalchemy.exc import SQLAlchemyError

from app.core.crypto import get_secret_cipher
from app.core.domain.errors import DomainError
from app.modules.agent.infrastructure.agent_host.repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    AgentRuntimeProfileRepository,
    ConversationRepository,
)
from app.modules.agent.services.mcp_pausing_calls import (
    close_pausing_tool_call,
    is_pausing_tool,
    record_pausing_tool_call,
)
from app.modules.agent.services.workspace_location import (
    ensure_recorded_location,
    pod_cwd_from_workspace_cwd,
)
from app.modules.agent.services.runtime_profile_service import (
    AgentRuntimeProfileService,
)
from app.modules.agent.tools.callable_tool_factory import inline_tool_schema_refs
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.dispatcher import AgentToolDispatcher
from app.modules.agent.tools.tool_errors import (
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
        except SuperTokensSessionError:
            # The token is not valid: expected traffic, and the denial below is
            # the whole answer.
            return False
        except Exception:
            # The auth backend could not answer. Same denial — a caller holding
            # a good token must not be let through because SuperTokens is down —
            # but this is an outage, not a bad token, and catching both as one
            # made the two indistinguishable from outside.
            logger.error(
                "agent.conversation_mcp_service.session_lookup.failed",
                exc_info=True,
            )
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

    async def parked_tool_return(
        self,
        *,
        conversation_id: UUID,
        tool_call_id: str,
    ) -> JsonObject | None:
        """The answer to a parked interaction, or ``None`` while it is pending.

        The host's MCP bridge polls this after `ask_user` or `request_approval`
        hands it a parked id, and hands the result back as that tool's return so
        the model never leaves its turn.

        Nothing new is stored to make this work. Deciding an interaction already
        writes a synthesized tool RETURN under the same durable id -- that is how
        the in-process resume replays the answer, and how re-running an approved
        tool is prevented -- so the pending/decided question is just "has that
        return been written yet".
        """
        async with self.uow_factory() as uow:
            message = await ConversationRepository(uow).get_tool_return(
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
        agent, conversation, ctx = await self._load_agent_context(
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
        )
        tools = await self.dispatcher.list_tools(
            agent=agent,
            conversation=conversation,
            ctx=ctx,
            agent_run_id=agent_run_id,
            # This route is reached only by the Agent Host MCP bridge, which is
            # the harness that has no other way to return a structured result.
            # The assembler applies the "does this run owe one?" gate.
            include_final_answer=True,
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
                result=error.structuredContent,
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
            # Records the cwd when nothing had recorded it, so metadata is
            # the source of truth in fact and not only by intention.
            workspace_location = await ensure_recorded_location(
                conversation, record=conversation_repo.set_conversation_metadata_key
            )
            runtime_profile = await self._resolved_runtime_profile(
                run=run,
                uow=uow,
                organization_id=conversation.organization_id,
                user_id=conversation.user_id,
            )
            ctx = BaseAgentContext(
                user_id=conversation.user_id,
                org_id=conversation.organization_id,
                pod_id=conversation.pod_id,
                conversation_id=conversation.id,
                agent_name=agent.name if agent is not None else None,
                agent_run_id=agent_run_id or (run.id if run is not None else None),
                runtime_profile=runtime_profile,
                # The runner computes these for the in-process harness, and this
                # bridge has to as well -- it is the tool path for *every*
                # remote harness, so anything left at its default is a default
                # the whole of Agent Host runs on.
                #
                # `vision_mode`: or a harness that reads images natively is
                # treated as text-only and delegates work it could do itself.
                vision_mode=vision_mode_from_runtime_profile(runtime_profile),
                # The location fields: without them `get_workspace_cwd()` falls
                # back to `/workspace/conversations/<uuid>`, so tools ran in a
                # directory the agent's own prompt does not name -- the prompt
                # says `/workspace/c/<date>/<slug>`, which is where the resolver
                # actually put the conversation, and which is where a previous
                # turn's files are. `pod_cwd` has the same effect on the pod
                # filesystem, scattering writes under `/me/conversations/<uuid>`.
                workspace_id=workspace_location.workspace_id,
                workspace_cwd=workspace_location.cwd,
                workspace_repo=workspace_location.repo,
                pod_cwd=pod_cwd_from_workspace_cwd(workspace_location.cwd),
                **surface_context_from_conversation(conversation),
            )
            return agent, conversation, ctx

    async def _resolved_runtime_profile(
        self,
        *,
        run: AgentRun | None,
        uow: SqlAlchemyUnitOfWork,
        organization_id: UUID | None,
        user_id: UUID,
    ) -> JsonObject | None:
        """The run's runtime, resolved, so its capabilities are actually present.

        `run.agent_runtime` is an `AgentRuntimeConfig` -- a profile id and a
        model name, and nothing else. It has no `model_capabilities`, so
        deriving the vision mode from it answered "this model cannot see" for
        every remote harness, whatever it was: a Claude Code or Codex host that
        reads images natively was told to delegate to a VISION_MODEL, and when
        none was configured, that PDF pages could not be viewed at all.

        Resolving is what produces capabilities, and `public_snapshot()` carries
        them precisely so the MCP bridges can rebuild a context. It costs one
        indexed lookup inside a unit of work this method already holds open, and
        no network call -- the model catalog lives on the profile.

        Falls back to the unresolved config on failure. A tool call must not
        fail because a profile was archived between the run starting and the
        model reaching for a file; the old behaviour was to delegate or refuse
        images, which is exactly what this returns to.
        """
        if run is None:
            return None
        stored = run.agent_runtime.model_dump(mode="json")
        try:
            service = AgentRuntimeProfileService(
                AgentRuntimeProfileRepository(uow, encryption=get_secret_cipher()),
                # Passed so a harness that has since reported it reads images is
                # believed, rather than the catalog copied from it before its
                # probe landed. Without this the resolve below is accurate about
                # a stale answer.
                AgentHostRepository(uow),
            )
            resolved = await service.resolve(
                runtime=run.agent_runtime,
                organization_id=organization_id,
                user_id=user_id,
            )
        except DomainError, RuntimeError, SQLAlchemyError:
            # Named rather than bare: these are what resolution actually fails
            # with -- an archived or deleted profile, a missing repository, a
            # database that will not answer. Anything else is a bug and should
            # surface as one rather than be absorbed into a silent fallback.
            logger.warning(
                "agent.conversation_mcp.runtime_resolve_failed.degraded",
                exc_info=True,
            )
            return stored
        return resolved.public_snapshot()


conversation_mcp_service = ConversationMCPService()


def _surface_platform(conversation: Conversation) -> str | None:
    metadata = conversation.metadata or {}
    platform = metadata.get("surface_platform") if isinstance(metadata, dict) else None
    return str(platform) if platform else None
