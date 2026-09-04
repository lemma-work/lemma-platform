"""Assembles the full toolset list available to an agent in a conversation.

One place resolves builtin toolsets, dynamic function/agent tools, and surface
platform tools, so the runner, the MCP services, and the approval executor all
see the exact same tools for a given (agent, conversation).
"""

from __future__ import annotations

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.domain.vision import AgentVisionMode
from app.modules.agent.tools.callable_tool_factory import AgentCallableToolFactory
from app.modules.agent.tools.toolset_selection import (
    AgentGrantSummary,
    resolve_toolset_names,
)
from app.modules.agent.tools.registry import (
    resolve_agent_toolsets,
)
from app.modules.agent.services.run_phase_spans import run_phase


async def load_agent_grant_summary(
    uow_factory: UnitOfWorkFactory, *, agent: Agent
) -> AgentGrantSummary:
    """This agent's grant summary, or an empty one where it cannot have grants.

    The pod default assistant is the empty case and not an error: it has no
    ``Agent`` row of its own, runs with the user's permissions, and takes its
    toolsets from the fixed default set rather than from anything granted to it.
    """
    if agent.pod_id is None or agent.id is None:
        return AgentGrantSummary()
    return await AgentCallableToolFactory(uow_factory).load_grant_summary(
        pod_id=agent.pod_id, agent_id=agent.id
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
        include_final_answer: bool = False,
        vision_mode: AgentVisionMode | None = None,
        grants: AgentGrantSummary | None = None,
    ) -> list[object]:
        """Every tool this (agent, conversation) can reach.

        ``grants`` lets a caller that already loaded the agent's grant summary
        (the runner does, to build its context brief) hand it over instead of
        paying for the same query twice. Callers without one -- the MCP server
        and the approval executor -- leave it unset and it is loaded here, so
        every path still resolves the same toolsets.
        """
        with run_phase("tool_assembly") as span:
            toolsets = await self._assemble(
                agent=agent,
                conversation=conversation,
                include_final_answer=include_final_answer,
                vision_mode=vision_mode,
                grants=grants,
            )
            span.set_attribute("lemma.toolsets", len(toolsets))
            return toolsets

    def _final_answer_toolsets(
        self,
        *,
        agent: Agent | None,
        conversation: Conversation | None,
        include_final_answer: bool,
    ) -> list[object]:
        """The `final_answer` tool, on the runs that reach it as a tool.

        Remote (Agent Host) runs only. The in-process LEMMA harness gets
        `final_answer` through pydantic-ai's `output_type`, so adding it here as
        well would expose the same tool twice -- hence opt-in from the caller
        rather than derived from the agent, which cannot tell the two harnesses
        apart.
        """
        if not include_final_answer or conversation is None:
            return []
        from app.modules.agent.tools.final_answer.final_answer_toolset import (
            build_final_answer_toolset,
            final_answer_expected,
        )

        if not final_answer_expected(agent=agent, conversation=conversation):
            return []
        return [
            build_final_answer_toolset(
                agent=agent,
                uow_factory=self.uow_factory if callable(self.uow_factory) else None,
            )
        ]

    async def _assemble(
        self,
        *,
        agent: Agent | None,
        conversation: Conversation | None,
        include_final_answer: bool,
        vision_mode: AgentVisionMode | None = None,
        grants: AgentGrantSummary | None = None,
    ) -> list[object]:
        if grants is None and agent is not None and callable(self.uow_factory):
            grants = await load_agent_grant_summary(self.uow_factory, agent=agent)
        toolset_names, allow_subagents = resolve_toolset_names(
            agent, conversation, grants=grants
        )
        toolsets: list[object] = list(resolve_agent_toolsets(toolset_names))
        # TODO is conversation-scoped (its list lives in conversation metadata), so
        # it isn't a static singleton in the registry — build it per conversation
        # here. Included in the assembled list so BOTH the in-process LEMMA harness
        # and the remote MCP path expose write_todos, and only when the agent's
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
                    grants=grants,
                )
            )
        if (
            conversation is not None
            and conversation.metadata
            and conversation.metadata.get("surface_platform")
        ):
            from app.modules.agent_surfaces.contracts.egress import build_surface_toolsets

            toolsets.extend(
                await build_surface_toolsets(self.uow_factory, conversation)
            )
        toolsets.extend(
            self._final_answer_toolsets(
                agent=agent,
                conversation=conversation,
                include_final_answer=include_final_answer,
            )
        )
        # Offered whenever the run can interpret an image at all -- directly, or
        # by delegating to a configured vision model, which answers in text and
        # so is safe on a text-only model.
        #
        # Here rather than in the runner, because the runner is not the only
        # assembler. A remote harness reaches every tool through the MCP server,
        # which re-assembles from scratch, so appending it in the runner left
        # `view_image` unreachable on every Agent Host run whatever its vision
        # mode -- while the run spec still advertised it, because that list is
        # the runner's copy. Prompts and `web_fetch`'s own result message tell
        # the model to use the tool unconditionally.
        if vision_mode is not None and vision_mode.can_see:
            from app.modules.agent.tools.workspace_cli.pydantic_adapter import (
                view_image_toolset,
            )

            if view_image_toolset not in toolsets:
                toolsets.append(view_image_toolset)
        return toolsets
