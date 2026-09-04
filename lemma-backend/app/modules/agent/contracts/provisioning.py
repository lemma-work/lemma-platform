"""What another module does to a pod's agents when it builds one.

Six operations, not `AgentService`. `sync_memory_folder_grant` is the reason
this is a contract and not a re-export: it lived only in the agent HTTP
controller, so an agent created straight through the service -- which is what a
bundle import does -- got the MEMORY toolset without the folder it writes to or
the grant that makes it writable. Invisible until MEMORY became a default for
new agents (#476); after that, exporting a pod and importing it back failed
outright. The source agent held `folder:/memory`, the export recorded the grant,
and applying it against a pod where nothing had created that folder raised
`400: Unknown resource name(s): folder:/memory`. It is published here so the
next caller that creates an agent without going through HTTP finds it.

A submodule rather than `contracts/__init__`: this reaches the service layer,
and `contracts/__init__` is imported by anything that wants any contract at all.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.core.domain.runtime import AgentRuntimeConfig
from app.modules.agent.api.dependencies import get_agent_service
from app.modules.agent.domain.entities import Agent
from app.modules.agent.domain.errors import AgentNotFoundError
from app.modules.agent.domain.value_objects import AgentToolset, JsonObject
from app.modules.agent.services.agent_memory_grant import sync_memory_folder_grant


async def list_agents(uow, *, pod_id: UUID, user_id: UUID, ctx: Context) -> list[Agent]:
    """Every agent in the pod this reader may see.

    Entities rather than names: a caller choosing which agents to copy needs
    `kind` to tell the pod's own assistant from the ones somebody made.
    """
    agents, _ = await get_agent_service(uow).list_agents(
        pod_id=pod_id, limit=1000, requester_user_id=user_id, ctx=ctx
    )
    return list(agents)


async def get_agent(uow, *, pod_id: UUID, name: str, ctx: Context) -> Agent | None:
    """The named agent, or ``None`` when the pod does not have one."""
    try:
        return await get_agent_service(uow).get_agent_by_name(
            pod_id=pod_id, name=name, ctx=ctx
        )
    except AgentNotFoundError:
        return None


async def require_agent(
    uow, *, pod_id: UUID, name: str, user_id: UUID, ctx: Context
) -> Agent:
    """The named agent, raising ``AgentNotFoundError`` when the pod has none."""
    return await get_agent_service(uow).get_agent_by_name(
        pod_id=pod_id, name=name, requester_user_id=user_id, ctx=ctx
    )


async def create_agent(
    uow,
    *,
    pod_id: UUID,
    user_id: UUID,
    name: str,
    instruction: str,
    description: str | None,
    icon_url: str | None,
    agent_runtime: AgentRuntimeConfig | None,
    toolsets: list[AgentToolset] | None,
    input_schema: JsonObject | None,
    output_schema: JsonObject | None,
    visibility: str | None,
    metadata: JsonObject | None,
    ctx: Context,
) -> Agent:
    """Create an agent, with the folder and grant its toolsets imply."""
    return await get_agent_service(uow).create_agent(
        pod_id=pod_id,
        user_id=user_id,
        name=name,
        instruction=instruction,
        description=description,
        icon_url=icon_url,
        agent_runtime=agent_runtime,
        toolsets=toolsets,
        input_schema=input_schema,
        output_schema=output_schema,
        visibility=visibility,
        metadata=metadata,
        ctx=ctx,
    )


async def update_agent(
    uow,
    *,
    pod_id: UUID,
    name: str,
    instruction: str | None,
    description: str | None,
    icon_url: str | None,
    agent_runtime: AgentRuntimeConfig | None,
    toolsets: list[AgentToolset] | None,
    input_schema: JsonObject | None,
    output_schema: JsonObject | None,
    metadata: JsonObject | None,
    user_id: UUID,
    ctx: Context,
) -> Agent:
    """Overwrite an existing agent's definition."""
    return await get_agent_service(uow).update_agent(
        pod_id=pod_id,
        name=name,
        instruction=instruction,
        description=description,
        icon_url=icon_url,
        agent_runtime=agent_runtime,
        toolsets=toolsets,
        input_schema=input_schema,
        output_schema=output_schema,
        metadata=metadata,
        requester_user_id=user_id,
        ctx=ctx,
    )


async def sync_agent_memory_grant(
    uow,
    *,
    pod_id: UUID,
    agent_id: UUID,
    toolsets: list[AgentToolset] | list[str] | None,
    ctx: Context,
    created_by_user_id: UUID,
) -> None:
    """Derive the `/memory` folder and grant an agent's MEMORY toolset implies.

    `create_agent` already derives it, so a caller needs this only after
    *replacing* an agent's grants: an inline grant list replaces every grant the
    agent holds, and a derived one applied first is the first thing wiped. That
    is the ordering the agent controller documents on its own two call sites.
    """
    await sync_memory_folder_grant(
        uow,
        pod_id=pod_id,
        agent_id=agent_id,
        toolsets=toolsets,
        ctx=ctx,
        created_by_user_id=created_by_user_id,
    )


__all__ = [
    "create_agent",
    "get_agent",
    "list_agents",
    "require_agent",
    "sync_agent_memory_grant",
    "update_agent",
]
