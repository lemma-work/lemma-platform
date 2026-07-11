"""Assembles the full toolset list available to an agent in a conversation.

One place resolves builtin toolsets, dynamic function/agent tools, and surface
platform tools, so the runner, the MCP services, and the approval executor all
see the exact same tools for a given (agent, conversation).
"""

from __future__ import annotations

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.tools.callable_tool_factory import AgentCallableToolFactory
from app.modules.agent.tools.registry import (
    POD_DEFAULT_AGENT_TOOLSETS,
    resolve_agent_toolsets,
)


class RunToolAssembler:
    """Builds the ordered toolset list for an agent run / tool call."""

    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory

    async def assemble(
        self,
        *,
        agent: Agent | None,
        conversation: Conversation | None,
    ) -> list[object]:
        # The pod default assistant (no specific agent) gets the fixed default
        # toolset. User-created agents get their configured toolsets plus narrow
        # runtime dependencies required to use them correctly.
        toolset_names = list(
            agent.toolsets if agent is not None else POD_DEFAULT_AGENT_TOOLSETS
        )
        # display_resource can author WIDGET content only after reading the
        # built-in lemma-widget skill. Make that dependency automatic so a
        # custom agent cannot receive USER_INTERACTION without the starter and
        # authoring contract it needs. This grants skill *reading* only; it does
        # not add POD, shell, network, or resource permissions.
        if (
            AgentToolset.USER_INTERACTION in toolset_names
            and AgentToolset.SKILLS not in toolset_names
        ):
            toolset_names.append(AgentToolset.SKILLS)
        # Depth=1: a run that IS itself a spawned sub-agent gets neither the
        # sub-agent control toolset nor the agent_<name> spawn tools. The source of
        # truth is the `is_sub_agent` metadata flag stamped by SubAgentService.spawn
        # — NOT parent_id, because a conversation can have a parent (e.g. pinned
        # under a PROJECT) without being a sub-agent, and such conversations keep
        # their spawning ability.
        conversation_metadata = (
            conversation.metadata
            if conversation is not None and isinstance(conversation.metadata, dict)
            else {}
        )
        allow_subagents = conversation is None or not conversation_metadata.get(
            "is_sub_agent"
        )
        if not allow_subagents:
            toolset_names = [t for t in toolset_names if t != AgentToolset.SUBAGENTS]
        toolsets: list[object] = list(resolve_agent_toolsets(toolset_names))
        # TODO is conversation-scoped (its list lives in conversation metadata), so
        # it isn't a static singleton in the registry — build it per conversation
        # here. Included in the assembled list so BOTH the in-process LEMMA harness
        # and the daemon MCP path expose write_todos, and only when the agent's
        # toolsets actually include TODO.
        if (
            conversation is not None
            and AgentToolset.TODO in toolset_names
            and callable(self.uow_factory)
        ):
            from app.modules.agent.capabilities.todo import build_todo_toolset

            toolsets.append(
                build_todo_toolset(
                    uow_factory=self.uow_factory,
                    conversation_id=conversation.id,
                )
            )
        if agent is not None and callable(self.uow_factory):
            toolsets.extend(
                await AgentCallableToolFactory(self.uow_factory).build_toolsets(
                    agent=agent,
                    allow_subagents=allow_subagents,
                )
            )
        if (
            conversation is not None
            and conversation.metadata
            and conversation.metadata.get("surface_platform")
        ):
            from app.composition.agent_surface_runtime import build_surface_toolsets

            toolsets.extend(
                await build_surface_toolsets(self.uow_factory, conversation)
            )
        return toolsets
