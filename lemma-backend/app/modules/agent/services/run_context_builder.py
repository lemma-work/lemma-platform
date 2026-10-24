"""Assembling the context one run executes against.

Everything the tools will see: who is asking, which pod and workspace, what the
conversation already knows, and how this model answers image-returning tools.

It is built once, before the model is called, because several of these settle
each other — the vision mode has to be decided before the toolset is assembled,
since the assembler offers `view_image` based on it, and the workspace cwd has
to be written down before any tool can resolve a relative path against it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.transient_errors import raise_if_database_unavailable
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.config import agent_settings
from app.modules.agent.domain.agent_memory_paths import (
    memory_is_active,
    memory_kept_after_turn,
)
from app.modules.agent.domain.entities import Agent, AgentRun, Conversation
from app.modules.agent.domain.run_origin import run_origin
from app.modules.agent.domain.value_objects import AgentToolset, HarnessKind
from app.modules.agent.domain.runtime_profiles import RuntimeModelCapability
from app.modules.agent.domain.vision import resolve_vision_mode
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.domain.private_notes import (
    keeper_started,
    run_delivers_to_surface,
)
from app.modules.agent.domain.run_context_prompt import (
    render_run_context,
    task_list_section,
)
from app.modules.agent.capabilities.open_questions import open_question_text
from app.modules.agent.services.agent_context_brief import AgentContextBriefBuilder
from app.modules.agent.services.attached_document_brief import (
    build_attached_document_section,
)
from app.modules.agent.services.outsider_brief import outsider_brief
from app.modules.agent.services.link_brief import link_brief
from app.modules.agent.services.brief_lines import run_source_of
from app.modules.agent.domain.agent_kind import AgentKind
from app.modules.agent.services.surface_context import surface_context_for_run
from app.modules.agent.domain.workspace_location import (
    has_recorded_cwd,
    pod_cwd_from_workspace_cwd,
    resolve_workspace_location,
)
from app.modules.agent.tools.context import ConversationContext
from app.modules.agent.tools.tool_assembler import load_agent_grant_summary
from app.modules.agent.tools.toolset_selection import resolve_toolset_names
from app.modules.agent.services.vision_service import vision_delegate_available


logger = get_logger(__name__)

_CONTEXT_BRIEF_UNAVAILABLE = (
    "## Runtime Context\n\n"
    "This section could not be assembled for this run, so the pod's tables, "
    "agents, files and your memory are not listed here. They still exist -- "
    "list them with your tools rather than concluding the pod is empty."
)


async def build_run_context(
    *,
    uow_factory: UnitOfWorkFactory,
    conversation: Conversation,
    agent: Agent,
    agent_run: AgentRun,
    user_id: UUID,
    resolved_runtime: Any,
    runtime_profile_snapshot: dict[str, object | None] | None,
    resolve_configured_accounts: Any,
) -> ConversationContext:
    """The context this run's tools execute against.

    `runtime_profile_snapshot` is passed in rather than recomputed: the caller needs the same snapshot for the run identity and
    the harness options, and two calls to `runtime_snapshot()` would be two
    objects that only happen to agree.
    """
    surface_context = surface_context_for_run(conversation, agent_run.metadata)
    workspace_location = resolve_workspace_location(conversation)
    # Same rule the MCP bridge follows: a conversation that never had a
    # cwd written down gets one now, so metadata is the source of truth
    # in fact. Costs nothing on the common path -- creation stamps it,
    # so this only opens a unit of work for a row that predates that.
    if not has_recorded_cwd(conversation):
        async with uow_factory() as uow:
            await ConversationRepository(uow).set_conversation_metadata_key(
                conversation.id, "cwd", workspace_location.cwd
            )
            await uow.commit()
        conversation.metadata = {
            **(conversation.metadata or {}),
            "cwd": workspace_location.cwd,
        }
    pod_cwd = pod_cwd_from_workspace_cwd(workspace_location.cwd)
    # Resolved from the run's *effective* toolsets, not the agent's configured
    # ones, so the always-on set, the grant-derived ones and the sub-agent
    # withholding are all accounted for -- the same list `RunToolAssembler`
    # builds tools from. The summary is carried on the context so the assembler
    # can reuse it instead of reading the same grants again.
    grant_summary = await load_agent_grant_summary(uow_factory, agent=agent)
    run_toolsets, _ = resolve_toolset_names(agent, conversation, grants=grant_summary)
    audience = Audience.of(conversation)
    for_outsider = audience.answers_outsiders
    memory_enabled = memory_is_active(run_toolsets)
    memory_after_turn = agent_settings.agent_memory_after_turn and (
        memory_kept_after_turn(
            memory_enabled=memory_enabled,
            in_process=resolved_runtime.harness_kind == HarnessKind.LEMMA,
            answers_outsiders=for_outsider,
            origin=run_origin(
                metadata=conversation.metadata,
                parent_id=conversation.parent_id,
                conversation_id=conversation.id,
            ).origin,
        )
    )
    ctx = ConversationContext(
        user_id=user_id,
        org_id=conversation.organization_id,
        pod_id=conversation.pod_id,
        conversation_id=conversation.id,
        agent_name=agent.name,
        agent_run_id=agent_run.id,
        workload_type="agent",
        workload_id=agent.id,
        # The owner's connector accounts are theirs; a stranger's run has no
        # connector tools to use them with, and must not be handed them anyway.
        configured_accounts=(
            {}
            if for_outsider
            else await resolve_configured_accounts(agent=agent, user_id=user_id)
        ),
        runtime_profile=runtime_profile_snapshot,
        workspace_id=workspace_location.workspace_id,
        workspace_cwd=workspace_location.cwd,
        machine=agent.machine,
        started_in_lemma=_started_in_lemma(conversation, user_id),
        workspace_repo=workspace_location.repo,
        pod_cwd=pod_cwd,
        # Only the in-process pydantic (LEMMA) harness catches the
        # ask_user/request_approval pause signal; remote harnesses run the
        # tools over MCP and own their own session, so they can't be paused
        # mid tool-call and use the WAITING output contract instead.
        supports_pause_signal=(resolved_runtime.harness_kind == HarnessKind.LEMMA),
        is_pod_default_agent=(agent.kind is AgentKind.POD_DEFAULT),
        memory_enabled=memory_enabled,
        memory_after_turn=memory_after_turn,
        run_toolsets=run_toolsets,
        workspace_readable=AgentToolset.WORKSPACE_CLI in run_toolsets,
        grant_summary=grant_summary,
        audience=audience,
        delivers_to_surface=run_delivers_to_surface(agent_run.metadata),
        keeper_asking=for_outsider and keeper_started(agent_run.metadata),
        **surface_context,
    )
    run_brief = ""
    try:
        if audience.asking_pod_id is not None:
            ctx.context_brief = await link_brief(
                uow_factory,
                pod_id=conversation.pod_id,
                asking_pod_id=audience.asking_pod_id,
                steward_user_id=user_id,
            )
        elif for_outsider:
            ctx.context_brief = await outsider_brief(
                uow_factory,
                pod_id=conversation.pod_id,
                owner_user_id=user_id,
                contact_id=audience.contact_id,
            )
        else:
            brief = await AgentContextBriefBuilder(uow_factory).build(
                agent=agent,
                conversation=conversation,
                toolsets=run_toolsets,
                user_id=user_id,
                pod_id=conversation.pod_id,
                # What started *this* run, as against how the conversation
                # began. A schedule stamps the conversation once and it stays
                # stamped, so only the run can say whether a person is here on
                # this turn.
                run_source=run_source_of(agent_run),
                platform=surface_context["surface_platform"],
            )
            ctx.context_brief, run_brief = brief.stable, brief.per_run
    # The failures a run should survive: a denied grant, a missing file, a
    # database or storage blip. Not a TypeError -- `agent_memory_brief` says a
    # real authorization failure "should fail the brief, not hide as an empty
    # memory section", and the `suppress(Exception)` this replaces hid exactly
    # that. A run could lose the pod's name, its tables, its grants and its
    # memory at once, with nothing logged and nothing said to the model, which
    # then saw a pod that appeared to be empty.
    except (DomainError, SQLAlchemyError, OSError, TimeoutError) as exc:
        raise_if_database_unavailable(exc)
        logger.warning(
            "agent.run.context_brief_unavailable.degraded",
            agent_id=str(agent.id),
            conversation_id=str(conversation.id),
            exc_info=True,
        )
        ctx.context_brief = _CONTEXT_BRIEF_UNAVAILABLE
    try:
        ctx.attached_document = await build_attached_document_section(
            uow_factory,
            conversation=conversation,
            pod_id=conversation.pod_id,
            user_id=user_id,
        )
    # Same survivable failures as the brief: without the section the agent
    # reads the doc itself, which is slower but not wrong.
    except DomainError, SQLAlchemyError, OSError, TimeoutError:
        logger.warning(
            "agent.run.attached_document_unavailable.degraded",
            conversation_id=str(conversation.id),
            exc_info=True,
        )
    # Never on a stranger's run: an open request is the owner's to answer.
    if not for_outsider:
        ctx.open_questions = await open_question_text(
            conversation_id=conversation.id,
            channel_id=getattr(conversation, "channel_id", None),
            pod_id=conversation.pod_id,
            user_id=conversation.user_id,
        )
    ctx.run_context = render_run_context(
        sections=(
            run_brief,
            ctx.attached_document,
            task_list_section(conversation, toolsets=run_toolsets),
            ctx.open_questions,
            await _machine_section(ctx, run_toolsets, for_outsider=for_outsider),
        )
    )
    # How image-returning tools answer on this run. Settled before the
    # toolset is built, because the assembler needs it: `view_image` is
    # offered whenever the mode can answer at all, and withholding it on
    # a text-only model never protected anything -- `pod_view_document_
    # pages` shipped image content to that same model anyway.
    supports_vision = RuntimeModelCapability.VISION in resolved_runtime.capabilities
    ctx.vision_mode = resolve_vision_mode(
        model_supports_vision=supports_vision,
        delegate_model_configured=vision_delegate_available(),
    )
    # Where this run's commands execute, decided once, here, for the whole run.
    # See `host_execution_selection`; imported here to keep it (and the
    # workspace module's host provider) out of every process's startup import
    # graph.
    from app.modules.agent.services.host_execution_selection import (
        choose_host_workspace,
    )
    from app.modules.agent.services.host_native_commands import (
        settle_native_commands,
    )

    if for_outsider:
        # No commands run for a stranger, so there is nowhere to run them --
        # and the owner's Mac is the last place one should land.
        return ctx
    if resolved_runtime.harness_kind == HarnessKind.LEMMA:
        ctx.host_workspace = await choose_host_workspace(
            conversation=conversation, agent_run=agent_run, user_id=user_id
        )
    else:
        ctx.host_runs_native_commands = await settle_native_commands(
            conversation=conversation,
            agent_run=agent_run,
            harness_id=_harness_id(runtime_profile_snapshot),
        )
    return ctx


def _harness_id(snapshot: dict[str, object | None] | None) -> UUID | None:
    """The Agent Host harness a coding-agent run's profile names, if any."""
    raw = (snapshot or {}).get("harness_id")
    try:
        return UUID(str(raw)) if raw else None
    except ValueError:
        return None


async def _machine_section(
    ctx: ConversationContext, toolsets, *, for_outsider: bool
) -> str:
    """The run's machine, for an agent that runs commands. Empty when it has
    none -- a stranger's run, or one with no machine it may use -- and when
    the machine cannot be read: a run is not worth failing over this."""
    if for_outsider or AgentToolset.WORKSPACE_CLI not in toolsets:
        return ""
    from app.modules.agent.domain.run_context_prompt import machine_section
    from app.modules.workspace.contracts.agent_machines import machine_brief

    try:
        brief = await machine_brief(
            ref=ctx.machine,
            user_id=ctx.user_id,
            pod_id=ctx.pod_id,
            agent_id=ctx.workload_id,
            agent_name=ctx.agent_name,
            conversation_id=ctx.conversation_id,
            run_id=ctx.conversation_id,
        )
    except DomainError, SQLAlchemyError, OSError, TimeoutError:
        logger.warning(
            "agent.run.machine_section_unavailable.degraded",
            conversation_id=str(ctx.conversation_id),
            exc_info=True,
        )
        return ""
    if brief is None:
        return ""
    return machine_section(
        ref=brief.ref,
        size=brief.size,
        docker=brief.docker,
        workdir=brief.workdir,
        disk_fraction=brief.disk_fraction,
        compute_left=brief.compute_left,
        compute_resets=brief.compute_resets,
    )


def _started_in_lemma(conversation, user_id) -> bool:
    """The same rule `contracts/run_origin` gives the machine resolver, read
    off the conversation this builder already holds."""
    from app.modules.agent.domain.value_objects import ConversationType

    return (
        conversation.user_id == user_id
        and getattr(conversation, "channel_id", None) is None
        and getattr(conversation, "contact_id", None) is None
        and str(getattr(conversation, "conversation_type", "CHAT"))
        not in (ConversationType.TASK.value, str(ConversationType.TASK))
    )
