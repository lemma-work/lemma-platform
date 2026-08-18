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
from app.modules.agent.tools.registry import (
    POD_DEFAULT_AGENT_TOOLSETS,
    resolve_agent_toolsets,
)
from app.modules.agent.services.run_phase_spans import run_phase


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
    ) -> list[object]:
        with run_phase("tool_assembly") as span:
            toolsets = await self._assemble(
                agent=agent,
                conversation=conversation,
                include_final_answer=include_final_answer,
                vision_mode=vision_mode,
            )
            span.set_attribute("lemma.toolsets", len(toolsets))
            return toolsets

    async def _assemble(
        self,
        *,
        agent: Agent | None,
        conversation: Conversation | None,
        include_final_answer: bool,
        vision_mode: AgentVisionMode | None = None,
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
            # A sub-agent that snoozes blocks its parent's tool call while the
            # parent is still mid-run and subject to its own limits — the parent
            # would sit waiting on a child that is deliberately asleep. Same
            # depth=1 rule, same reason.
            toolset_names = [t for t in toolset_names if t != AgentToolset.SNOOZE]
            # Messaging is withheld for a different reason: a sub-agent is an
            # implementation detail of its parent's turn, and a colleague
            # receiving a message from one has no way to place it. Whatever needs
            # saying, the parent should say — it is the thing with the context and
            # the attribution.
            toolset_names = [t for t in toolset_names if t != AgentToolset.MESSAGING]
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
        # Remote (Agent Host) runs only. The in-process LEMMA harness gets
        # final_answer through pydantic-ai's output_type, so adding it here too
        # would expose the same tool twice — hence opt-in rather than derived
        # from the agent, which cannot tell the two harnesses apart.
        if include_final_answer and conversation is not None:
            from app.modules.agent.tools.final_answer.final_answer_toolset import (
                build_final_answer_toolset,
                final_answer_expected,
            )

            if final_answer_expected(agent=agent, conversation=conversation):
                toolsets.append(
                    build_final_answer_toolset(
                        agent=agent,
                        uow_factory=(
                            self.uow_factory if callable(self.uow_factory) else None
                        ),
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
