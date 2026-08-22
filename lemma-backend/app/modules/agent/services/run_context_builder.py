"""Assembling the context one run executes against.

Everything the tools will see: who is asking, which pod and workspace, what the
conversation already knows, and how this model answers image-returning tools.

It is built once, before the model is called, because several of these settle
each other — the vision mode has to be decided before the toolset is assembled,
since the assembler offers `view_image` based on it, and the workspace cwd has
to be written down before any tool can resolve a relative path against it.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import HarnessKind
from app.modules.agent.domain.runtime_profiles import RuntimeModelCapability
from app.modules.agent.domain.vision import resolve_vision_mode
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.agent_context_brief import AgentContextBriefBuilder
from app.modules.agent.services.conversation_access import POD_ASSISTANT_AGENT_ID
from app.modules.agent.services.surface_context import (
    surface_context_from_conversation,
)
from app.modules.agent.services.workspace_location import (
    has_recorded_cwd,
    pod_cwd_from_workspace_cwd,
    resolve_workspace_location,
)
from app.modules.agent.tools.context import ConversationContext
from app.modules.agent.services.vision_service import vision_delegate_available


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
        is_pod_default_agent=(agent.id == POD_ASSISTANT_AGENT_ID),
        **surface_context,
    )
    with suppress(Exception):
        ctx.context_brief = await AgentContextBriefBuilder(uow_factory).build(
            agent=agent,
            conversation=conversation,
            user_id=user_id,
            pod_id=conversation.pod_id,
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
    return ctx
