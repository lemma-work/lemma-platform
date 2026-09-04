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

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.agent_memory_paths import memory_is_active
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import HarnessKind
from app.modules.agent.domain.runtime_profiles import RuntimeModelCapability
from app.modules.agent.domain.vision import resolve_vision_mode
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.agent_context_brief import AgentContextBriefBuilder
from app.modules.agent.domain.agent_kind import AgentKind
from app.modules.agent.services.surface_context import (
    surface_context_from_conversation,
)
from app.modules.agent.services.workspace_location import (
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
    agent_run_id: UUID,
    user_id: UUID,
    resolved_runtime: Any,
    runtime_profile_snapshot: dict[str, object | None] | None,
    runtime_credentials: dict[str, Any],
    resolve_configured_accounts: Any,
) -> ConversationContext:
    """The context this run's tools execute against.

    `runtime_profile_snapshot` and `runtime_credentials` are passed in rather
    than recomputed: the caller needs the same snapshot for the run identity and
    the harness options, and two calls to `public_snapshot()` would be two
    objects that only happen to agree.
    """
    surface_context = surface_context_from_conversation(conversation)
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
    ctx = ConversationContext(
        user_id=user_id,
        org_id=conversation.organization_id,
        pod_id=conversation.pod_id,
        conversation_id=conversation.id,
        agent_name=agent.name,
        agent_run_id=agent_run_id,
        workload_type="agent",
        workload_id=agent.id,
        configured_accounts=await resolve_configured_accounts(
            agent=agent,
            user_id=user_id,
        ),
        runtime_profile=runtime_profile_snapshot,
        runtime_credentials=runtime_credentials,
        workspace_id=workspace_location.workspace_id,
        workspace_cwd=workspace_location.cwd,
        workspace_repo=workspace_location.repo,
        pod_cwd=pod_cwd,
        # Only the in-process pydantic (LEMMA) harness catches the
        # ask_user/request_approval pause signal; remote harnesses run the
        # tools over MCP and own their own session, so they can't be paused
        # mid tool-call and use the WAITING output contract instead.
        supports_pause_signal=(resolved_runtime.harness_kind == HarnessKind.LEMMA),
        is_pod_default_agent=(agent.kind is AgentKind.POD_DEFAULT),
        memory_enabled=memory_is_active(run_toolsets),
        grant_summary=grant_summary,
        **surface_context,
    )
    try:
        ctx.context_brief = await AgentContextBriefBuilder(uow_factory).build(
            agent=agent,
            conversation=conversation,
            toolsets=run_toolsets,
            user_id=user_id,
            pod_id=conversation.pod_id,
        )
    # The failures a run should survive: a denied grant, a missing file, a
    # database or storage blip. Not a TypeError -- `agent_memory_brief` says a
    # real authorization failure "should fail the brief, not hide as an empty
    # memory section", and the `suppress(Exception)` this replaces hid exactly
    # that. A run could lose the pod's name, its tables, its grants and its
    # memory at once, with nothing logged and nothing said to the model, which
    # then saw a pod that appeared to be empty.
    except DomainError, SQLAlchemyError, OSError, TimeoutError:
        logger.warning(
            "agent.run.context_brief_unavailable.degraded",
            agent_id=str(agent.id),
            conversation_id=str(conversation.id),
            exc_info=True,
        )
        ctx.context_brief = _CONTEXT_BRIEF_UNAVAILABLE
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
    return ctx
