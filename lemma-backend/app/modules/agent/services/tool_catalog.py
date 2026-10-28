"""Which tools a run gets: ``specs_for``.

One answer for every caller -- the runner, the Agent Host's MCP relay and the
approval executor -- so a tool that lists is a tool that dispatches. The
toolsets a run is entitled to are ``domain/toolsets``; what is decided here is
which specs those become for this conversation: the per-run tools (todo,
granted functions and agents, the chat platform's own, a contact's, a web
visitor's form, a call's voice, ``final_answer``, ``view_image``), where the
run's commands execute, and what a run answering somebody outside the pod may
keep -- by each spec's policy.
"""

from __future__ import annotations

from enum import StrEnum

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.domain.tools import ToolGroup, ToolSpec
from app.modules.agent.domain.toolsets import AgentGrantSummary, resolve_toolset_names
from app.modules.agent.domain.value_objects import AgentToolset, HarnessKind
from app.modules.agent.infrastructure.tools.browser import tools as browser
from app.modules.agent.infrastructure.tools.browser import vm_browser
from app.modules.agent.infrastructure.tools.callables import (
    AgentCallableToolFactory,
    load_agent_grant_summary,
)
from app.modules.agent.infrastructure.tools.connectors import tools as connectors
from app.modules.agent.infrastructure.tools.contact import contact_specs
from app.modules.agent.infrastructure.tools.form import form_specs
from app.modules.agent.infrastructure.tools.messaging import answer
from app.modules.agent.infrastructure.tools.messaging import tools as messaging
from app.modules.agent.infrastructure.tools.pod import tools as pod
from app.modules.agent.infrastructure.tools.skills import tools as skills
from app.modules.agent.infrastructure.tools.speech import tools as speech
from app.modules.agent.infrastructure.tools.subagents import tools as subagents
from app.modules.agent.infrastructure.tools.user_interaction import (
    tools as user_interaction,
)
from app.modules.agent.infrastructure.tools.waiting import tools as waiting
from app.modules.agent.infrastructure.tools.web import tools as web
from app.modules.agent.infrastructure.tools.workspace_cli import tools as workspace_cli
from app.modules.agent.services.run_phase_spans import run_phase


class ToolSurface(StrEnum):
    """Where a run's tools are served, which decides a few of them."""

    #: The pydantic-ai harness in this worker: ``final_answer`` is its output
    #: type, and ``answer_question`` only rides when a question is open.
    IN_PROCESS = "in_process"
    #: A coding agent over the run's MCP relay: everything is a tool.
    AGENT_HOST = "agent_host"
    #: A recorded call run again: an approved call after its pause, or one a
    #: resumed turn settles.
    RERUN = "rerun"

    @classmethod
    def for_harness(cls, kind: HarnessKind) -> ToolSurface:
        return cls.IN_PROCESS if kind == HarnessKind.LEMMA else cls.AGENT_HOST


#: The toolsets that are the same for every run that has them. TODO is built
#: per run (it needs a unit of work) and MEMORY carries no tools.
STATIC_SPECS: dict[AgentToolset, tuple[ToolSpec, ...]] = {
    AgentToolset.WORKSPACE_CLI: workspace_cli.SPECS,
    AgentToolset.BROWSER: browser.SPECS,
    AgentToolset.SKILLS: skills.SPECS,
    AgentToolset.WEB_SEARCH: web.SPECS,
    AgentToolset.USER_INTERACTION: user_interaction.SPECS,
    AgentToolset.SPEECH: speech.SPECS,
    AgentToolset.POD: pod.SPECS,
    AgentToolset.SUBAGENTS: subagents.SPECS,
    AgentToolset.CONNECTORS: connectors.SPECS,
    AgentToolset.WAIT: waiting.SPECS,
    AgentToolset.MESSAGING: messaging.SPECS,
    AgentToolset.VIEW_IMAGE: workspace_cli.VIEW_IMAGE_SPECS,
}


async def specs_for(
    *,
    agent: Agent | None,
    conversation: Conversation,
    ctx: ToolContext,
    surface: ToolSurface,
    uow_factory: UnitOfWorkFactory | None,
    grants: AgentGrantSummary | None = None,
) -> list[ToolSpec]:
    """Every tool this run can reach, in the order its model is shown them.

    ``grants`` spares a caller that already read them a second query.
    ``uow_factory`` is ``None`` only where nothing is to be read or written,
    which leaves out the tools that need a unit of work.
    """
    with run_phase("tool_assembly") as span:
        specs = await _assemble(
            agent=agent,
            conversation=conversation,
            ctx=ctx,
            surface=surface,
            uow_factory=uow_factory,
            grants=grants,
        )
        span.set_attribute("lemma.toolsets", len({spec.toolset for spec in specs}))
        return specs


async def _assemble(
    *,
    agent: Agent | None,
    conversation: Conversation,
    ctx: ToolContext,
    surface: ToolSurface,
    uow_factory: UnitOfWorkFactory | None,
    grants: AgentGrantSummary | None,
) -> list[ToolSpec]:
    if grants is None and agent is not None and uow_factory is not None:
        grants = await load_agent_grant_summary(uow_factory, agent=agent)
    names, allow_subagents = resolve_toolset_names(agent, conversation, grants=grants)
    specs = [spec for name in names for spec in STATIC_SPECS.get(name, ())]
    if AgentToolset.TODO in names and uow_factory is not None:
        from app.modules.agent.infrastructure.tools.todo import todo_specs

        specs.extend(todo_specs(uow_factory=uow_factory))
    audience = Audience.of(conversation)
    if not audience.answers_outsiders and uow_factory is not None:
        specs.extend(
            await _members_specs(
                agent=agent,
                conversation=conversation,
                ctx=ctx,
                uow_factory=uow_factory,
                allow_subagents=allow_subagents,
                grants=grants,
            )
        )
    specs.extend(_outsider_specs(ctx, audience, uow_factory))
    specs.extend(_marked_specs(agent, conversation, surface, uow_factory))
    specs = _seen_and_placed(specs, ctx, surface, audience)
    return [spec for spec in specs if _offered(spec, audience)]


async def _members_specs(
    *,
    agent: Agent | None,
    conversation: Conversation,
    ctx: ToolContext,
    uow_factory: UnitOfWorkFactory,
    allow_subagents: bool,
    grants: AgentGrantSummary | None,
) -> list[ToolSpec]:
    """The agent's functions and sub-agents, and its platform's tools.

    Never on a run answering somebody outside the pod: a function runs on
    behalf of the conversation's user, who on such a run is the member
    looking after it, not the stranger asking.
    """
    specs: list[ToolSpec] = []
    if agent is not None:
        specs.extend(
            await AgentCallableToolFactory(uow_factory).build_specs(
                agent=agent, allow_subagents=allow_subagents, grants=grants
            )
        )
    if ctx.surface_platform:
        # The platform the run talks over: none for a run asked in Lemma,
        # whichever platform its conversation was last heard on.
        from app.modules.agent_surfaces.contracts.egress import build_surface_tools

        specs.extend(
            await build_surface_tools(
                uow_factory,
                platform=ctx.surface_platform,
                surface_id=ctx.surface_id,
                conversation_kind=ctx.surface_conversation_kind,
            )
        )
    return specs


def _marked_specs(
    agent: Agent | None,
    conversation: Conversation,
    surface: ToolSurface,
    uow_factory: UnitOfWorkFactory | None,
) -> list[ToolSpec]:
    """Tools a run gets by what it is rather than what it declared.

    Imported here, as the tools that are rarely offered: an app import would
    otherwise load them for every request.
    """
    from app.modules.agent.infrastructure.tools.call import tools as call
    from app.modules.agent.infrastructure.tools.final_answer.tools import (
        final_answer_expected,
        final_answer_spec,
    )

    specs: list[ToolSpec] = []
    if conversation.call is not None and conversation.call.live_coordinator:
        # By the call mark, not a declared toolset: an agent gets the voice by
        # being called, and only while a socket holds the call -- a chat a call
        # happened in has no voice to relay to once it is over. Its sub-agents
        # show things; only it speaks.
        specs.extend(call.SPECS)
    if surface is ToolSurface.AGENT_HOST and final_answer_expected(
        agent=agent, conversation=conversation
    ):
        specs.append(final_answer_spec(agent=agent, uow_factory=uow_factory))
    return specs


def _seen_and_placed(
    specs: list[ToolSpec], ctx: ToolContext, surface: ToolSurface, audience: Audience
) -> list[ToolSpec]:
    """What the run's vision, its host and its open questions add or take away."""
    # `view_image` whenever the run can interpret an image at all, directly or
    # through a vision delegate; never for a stranger, since it opens files in
    # the owner's sandbox.
    if (
        not audience.answers_outsiders
        and ctx.vision_mode.can_see
        and ToolGroup.VIEW_IMAGE not in {spec.toolset for spec in specs}
    ):
        specs.extend(workspace_cli.VIEW_IMAGE_SPECS)
    if surface is not ToolSurface.RERUN and ctx.host_execution_mode is not None:
        specs = _for_host_execution(specs, native=ctx.host_execution_mode == "native")
    # Answering what was asked of somebody is theirs to do, in their own
    # thread; a stranger's turn must not do it for them. In process it rides
    # only while a question is open.
    if not audience.answers_outsiders and (
        surface is not ToolSurface.IN_PROCESS or ctx.open_questions
    ):
        specs.extend(answer.SPECS)
    return specs


def _offered(spec: ToolSpec, audience: Audience) -> bool:
    return spec.policy.outsider_allowed or not audience.answers_outsiders


def _outsider_specs(
    ctx: ToolContext,
    audience: Audience,
    uow_factory: UnitOfWorkFactory | None,
) -> list[ToolSpec]:
    """A contact's own rows and functions, where they alone read the answer
    (not in a group, where everybody would read what was quoted), and the form
    on a web visitor's page."""
    if uow_factory is None:
        return []
    specs: list[ToolSpec] = []
    if audience.is_contact and ctx.place_kind != "CHANNEL":
        specs.extend(contact_specs(uow_factory=uow_factory))
    if audience.answers_outsiders and ctx.on_web_widget:
        specs.extend(form_specs(uow_factory=uow_factory))
    return specs


def _for_host_execution(specs: list[ToolSpec], *, native: bool) -> list[ToolSpec]:
    """The tools of a run whose commands execute on the person's Mac.

    Natively (an Agent Host run) the coding agent already has a shell there,
    so Lemma's command tools are withheld. Either way the browser the person
    watches is still in the VM, reached through the shell that is now
    elsewhere, so a run that had the shell gets ``browser`` in its place.
    """
    had_shell = any(spec.toolset is ToolGroup.WORKSPACE_CLI for spec in specs)
    if native:
        specs = [spec for spec in specs if not spec.policy.native_command]
    if had_shell and not any(spec.toolset is ToolGroup.VM_BROWSER for spec in specs):
        specs = [*specs, *vm_browser.SPECS]
    return specs


__all__ = [
    "STATIC_SPECS",
    "ToolSurface",
    "specs_for",
]
