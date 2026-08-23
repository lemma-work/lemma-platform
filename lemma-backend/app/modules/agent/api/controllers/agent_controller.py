"""Pod agent definition routes."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import PodContextDep
from app.core.authorization.grants import (
    apply_inline_workload_grants,
    list_grantee_resource_grants,
    normalize_pod_resource_grants,
    replace_grantee_resource_grants,
    validate_pod_resource_grant_permissions,
)
from app.core.api.pagination import parse_uuid_page_token
from app.core.helpers.slug import normalize_resource_name
from app.modules.agent.api.dependencies import (
    AgentResourceDeleteDep,
    AgentResourceEditorDep,
    AgentResourceViewerDep,
    AgentServiceDep,
    AgentViewerDep,
)
from app.modules.agent.services.agent_memory_grant import sync_memory_folder_grant
from app.modules.agent.api.schemas import (
    AgentActionResponse,
    AgentDetailResponse,
    AgentListResponse,
    AgentMessageResponse,
    AgentPermissionsReplaceRequest,
    AgentPermissionsResponse,
    AgentResponse,
    AgentResourcePermissionResponse,
    AgentSummaryResponse,
    CreateAgentRequest,
    UpdateAgentRequest,
)
from app.modules.agent.domain.entities import Agent

router = APIRouter(prefix="/pods/{pod_id}/agents", tags=["agents"])


def _agent_response(agent: Agent) -> AgentResponse:
    return AgentResponse.model_validate(agent)


async def _agent_action_response(agent: Agent) -> AgentActionResponse:
    return AgentActionResponse(
        **_agent_response(agent).model_dump(),
        allowed_actions=agent.allowed_actions,
    )


def _agent_summary_response(
    agent: Agent,
    grants: list[AgentResourcePermissionResponse] | None = None,
) -> AgentSummaryResponse:
    # `allowed_actions`, `toolsets` and `metadata` all live on the entity, so
    # from_attributes validation picks them up directly.
    summary = AgentSummaryResponse.model_validate(agent)
    summary.has_pinned_runtime = bool(
        getattr(getattr(agent, "agent_runtime", None), "profile_id", None)
    )
    # An empty object and a `properties` key with nothing under it both mean
    # "declares no inputs" — the agent builder writes the second when someone
    # opens the schema editor and adds nothing.
    input_schema = getattr(agent, "input_schema", None) or {}
    summary.takes_input = bool(
        isinstance(input_schema, dict) and input_schema.get("properties")
    )
    summary.grants = grants
    return summary


@router.post(
    "",
    response_model=AgentActionResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="agent.create",
    summary="Create Agent",
    description=(
        "Create a pod-owned agent definition with runtime, toolsets, and schemas."
    ),
)
async def create_agent(
    pod_id: UUID,
    data: CreateAgentRequest,
    user: CurrentUser,
    service: AgentServiceDep,
    uow: UoWDep,
    ctx: PodContextDep,
) -> AgentActionResponse:
    agent = await service.create_agent(
        pod_id=pod_id,
        user_id=user.id,
        name=normalize_resource_name(data.name),
        description=data.description,
        icon_url=data.icon_url,
        instruction=data.instruction,
        agent_runtime=data.agent_runtime,
        toolsets=data.toolsets,
        input_schema=data.input_schema,
        output_schema=data.output_schema,
        visibility=data.visibility,
        metadata=data.metadata,
        ctx=ctx,
    )
    agent = await service.get_agent_by_name(
        pod_id=pod_id,
        name=agent.name,
        requester_user_id=user.id,
        ctx=ctx,
    )
    # Inline grants: apply resource permissions in the same request so callers
    # don't have to follow create with a separate permissions-replace call (which
    # previously was the *only* way grants stuck — passing them to create used to
    # silently no-op). Same session as the create above, so it's atomic.
    await apply_inline_workload_grants(
        uow.session,
        pod_id=pod_id,
        grantee_type="AGENT",
        grantee_id=agent.id,
        permissions=data.permissions,
        created_by_user_id=user.id,
    )
    # After the block above, never before it: an inline `permissions` list
    # REPLACES this agent's grants, so a memory grant applied first would be the
    # first thing wiped. Derived from the toolsets, so it also comes back on the
    # next save and goes away when MEMORY does.
    await sync_memory_folder_grant(
        uow,
        pod_id=pod_id,
        agent_id=agent.id,
        toolsets=data.toolsets,
        ctx=ctx,
        created_by_user_id=user.id,
    )
    return await _agent_action_response(agent)


@router.get(
    "",
    response_model=AgentListResponse,
    operation_id="agent.list",
    summary="List Agents",
    description="List pod-owned agent definitions visible to the current user.",
    dependencies=[AgentViewerDep],
)
async def list_agents(
    pod_id: UUID,
    user: CurrentUser,
    service: AgentServiceDep,
    ctx: PodContextDep,
    uow: UoWDep,
    page_token: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    include: list[str] = Query(
        default_factory=list,
        description=(
            "Extra data to embed. `permissions` attaches each agent's resource "
            "grants, resolved for the whole page in one query — without it, a "
            "caller that needs grants must call the per-agent permissions "
            "endpoint once per row."
        ),
    ),
) -> AgentListResponse:
    agents, next_cursor = await service.list_agents(
        pod_id=pod_id,
        cursor=parse_uuid_page_token(page_token),
        limit=limit,
        requester_user_id=user.id,
        ctx=ctx,
    )
    grants_by_agent = await _grants_for_agents(uow, pod_id, agents, include)
    return AgentListResponse(
        items=[
            _agent_summary_response(
                item,
                grants_by_agent.get(item.id) if grants_by_agent is not None else None,
            )
            for item in agents
        ],
        limit=limit,
        next_page_token=str(next_cursor) if next_cursor else None,
    )


async def _grants_for_agents(
    uow: Any,
    pod_id: UUID,
    agents: list[Agent],
    include: list[str],
) -> dict[UUID, list[AgentResourcePermissionResponse]] | None:
    """Grants for a whole page of agents, or None when not requested."""
    if not any(part.strip().lower() == "permissions" for part in include):
        return None
    from app.core.authorization.grants import list_grants_for_grantees

    ids = [agent.id for agent in agents if agent.id is not None]
    grouped = await list_grants_for_grantees(
        uow.session, pod_id=pod_id, grantee_type="AGENT", grantee_ids=ids
    )
    return {
        agent_id: [
            AgentResourcePermissionResponse(
                resource_type=resource_type,
                resource_name=resource_name,
                permission_ids=sorted(set(permission_ids)),
            )
            for (resource_type, resource_name), permission_ids in grants.items()
        ]
        for agent_id, grants in grouped.items()
    }


@router.get(
    "/{agent_name}",
    response_model=AgentDetailResponse,
    operation_id="agent.get",
    summary="Get Agent",
    description="Get one pod-owned agent definition by its stable name.",
    dependencies=[AgentResourceViewerDep],
)
async def get_agent(
    pod_id: UUID,
    agent_name: str,
    user: CurrentUser,
    service: AgentServiceDep,
    uow: UoWDep,
    ctx: PodContextDep,
) -> AgentDetailResponse:
    agent = await service.get_agent_by_name(
        pod_id=pod_id,
        name=agent_name,
        requester_user_id=user.id,
        ctx=ctx,
    )
    response = await _agent_action_response(agent)
    return AgentDetailResponse(
        **response.model_dump(),
        permissions=await _agent_permissions_response(
            uow,
            pod_id=pod_id,
            agent=agent,
        ),
    )


@router.get(
    "/{agent_name}/permissions",
    response_model=AgentPermissionsResponse,
    operation_id="agent.permissions.get",
    summary="Get Agent Resource Permissions",
    description="Get explicit resource grants assigned to an agent.",
    dependencies=[AgentResourceViewerDep],
)
async def get_agent_permissions(
    pod_id: UUID,
    agent_name: str,
    user: CurrentUser,
    service: AgentServiceDep,
    uow: UoWDep,
    ctx: PodContextDep,
) -> AgentPermissionsResponse:
    agent = await service.get_agent_by_name(
        pod_id=pod_id,
        name=agent_name,
        requester_user_id=user.id,
        ctx=ctx,
    )
    return await _agent_permissions_response(uow, pod_id=pod_id, agent=agent)


@router.put(
    "/{agent_name}/permissions",
    response_model=AgentPermissionsResponse,
    operation_id="agent.permissions.replace",
    summary="Replace Agent Resource Permissions",
    description="Replace explicit resource grants assigned to an agent.",
    # Editing an agent's wiring is editing the agent: same permission as the
    # PATCH above. It used to require agent.delete, which pod editors do not
    # hold — so the people who build agents could not rewire the ones they
    # had just built. That gate never contained anything either: create
    # applies the same grants inline (below) on agent.create alone, so an
    # editor could always reach any grant set by making a new agent.
    dependencies=[AgentResourceEditorDep],
)
async def replace_agent_permissions(
    pod_id: UUID,
    agent_name: str,
    data: AgentPermissionsReplaceRequest,
    user: CurrentUser,
    service: AgentServiceDep,
    uow: UoWDep,
    ctx: PodContextDep,
) -> AgentPermissionsResponse:
    agent = await service.get_agent_by_name(
        pod_id=pod_id,
        name=agent_name,
        requester_user_id=user.id,
        ctx=ctx,
    )
    validate_pod_resource_grant_permissions(data.grants)
    grants = await normalize_pod_resource_grants(
        uow.session,
        pod_id=pod_id,
        grants=data.grants,
    )
    await replace_grantee_resource_grants(
        uow.session,
        pod_id=pod_id,
        grantee_type="AGENT",
        grantee_id=agent.id,
        grants=grants,
        created_by_user_id=user.id,
    )
    # This endpoint replaces every grant the agent holds, memory's included, so
    # it has to be put back -- otherwise editing any permission is a way to
    # silently disable a capability that is still switched on.
    await sync_memory_folder_grant(
        uow,
        pod_id=pod_id,
        agent_id=agent.id,
        toolsets=agent.toolsets,
        ctx=ctx,
        created_by_user_id=user.id,
    )
    return await _agent_permissions_response(uow, pod_id=pod_id, agent=agent)


@router.patch(
    "/{agent_name}",
    response_model=AgentActionResponse,
    operation_id="agent.update",
    summary="Update Agent",
    description=(
        "Update an agent definition, including prompt instruction, runtime, "
        "toolsets, and schemas."
    ),
    dependencies=[AgentResourceEditorDep],
)
async def update_agent(
    pod_id: UUID,
    agent_name: str,
    data: UpdateAgentRequest,
    user: CurrentUser,
    service: AgentServiceDep,
    uow: UoWDep,
    ctx: PodContextDep,
) -> AgentActionResponse:
    update_payload = data.model_dump(exclude_unset=True)
    # Grants are not a column on the agent; they go to the grants table below.
    update_payload.pop("permissions", None)
    if "agent_runtime" in update_payload:
        update_payload["agent_runtime"] = data.agent_runtime
    agent = await service.update_agent(
        pod_id=pod_id,
        name=agent_name,
        requester_user_id=user.id,
        ctx=ctx,
        **update_payload,
    )
    assert agent.id is not None
    # Same request as the update above, matching create. Without this an author
    # could create an agent with its grants and then silently lose them on the
    # next edit — the block was accepted and dropped.
    await apply_inline_workload_grants(
        uow.session,
        pod_id=pod_id,
        grantee_type="AGENT",
        grantee_id=agent.id,
        permissions=data.permissions,
        created_by_user_id=user.id,
    )
    # From the agent as it now stands, not from the request: a PATCH that never
    # mentions toolsets must not read as "memory off".
    await sync_memory_folder_grant(
        uow,
        pod_id=pod_id,
        agent_id=agent.id,
        toolsets=agent.toolsets,
        ctx=ctx,
        created_by_user_id=user.id,
    )
    return await _agent_action_response(agent)


@router.delete(
    "/{agent_name}",
    response_model=AgentMessageResponse,
    operation_id="agent.delete",
    summary="Delete Agent",
    description="Delete a pod-owned agent definition by name.",
    dependencies=[AgentResourceDeleteDep],
)
async def delete_agent(
    pod_id: UUID,
    agent_name: str,
    user: CurrentUser,
    service: AgentServiceDep,
    ctx: PodContextDep,
) -> AgentMessageResponse:
    await service.delete_agent(
        pod_id=pod_id,
        name=agent_name,
        requester_user_id=user.id,
        ctx=ctx,
    )
    return AgentMessageResponse(message=f"Agent {agent_name} deleted successfully")


async def _agent_permissions_response(
    uow: UoWDep,
    *,
    pod_id: UUID,
    agent: Agent,
) -> AgentPermissionsResponse:
    grouped = await list_grantee_resource_grants(
        uow.session,
        pod_id=pod_id,
        grantee_type="AGENT",
        grantee_id=agent.id,
    )
    return AgentPermissionsResponse(
        agent_id=agent.id,
        agent_name=agent.name,
        grants=[
            AgentResourcePermissionResponse(
                resource_type=resource_type,
                resource_name=resource_name,
                permission_ids=sorted(set(permission_ids)),
            )
            for (resource_type, resource_name), permission_ids in grouped.items()
        ],
    )
