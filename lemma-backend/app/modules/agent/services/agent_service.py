"""Application service for pod-owned agents."""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import (
    ActorType,
    Context,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.permissions import Permissions
from app.modules.agent.domain.entities import Agent
from app.modules.agent.domain.errors import (
    AgentAlreadyExistsError,
    AgentNotFoundError,
    AgentValidationError,
)
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    AgentToolset,
    JsonObject,
)
from app.modules.agent.domain.ports import AgentRepository

_UNSET = object()
def _normalize_agent_visibility(value: ResourceVisibility | str | None) -> str:
    if value is None:
        return ResourceVisibility.POD.value
    raw = value.value if isinstance(value, ResourceVisibility) else str(value)
    try:
        visibility = ResourceVisibility(raw.upper())
    except ValueError as exc:
        raise AgentValidationError(f"Invalid visibility: {value}") from exc
    return visibility.value


class AgentService:
    """Create and read pod-owned agent definitions."""

    def __init__(
        self,
        *,
        agent_repository: AgentRepository,
        authorization_service: object,
    ):
        self.agent_repository = agent_repository
        self.authorization_service = authorization_service

    async def _require_action(
        self,
        *,
        requester_user_id: UUID | None,
        action: str,
        pod_id: UUID,
        agent_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> None:
        if ctx is not None:
            await ctx.require(
                action,
                ResourceRef(
                    resource_type=ResourceType.AGENT if agent_id else ResourceType.POD,
                    resource_id=agent_id or pod_id,
                    pod_id=pod_id,
                ),
            )
            return
        if requester_user_id is None:
            return
        raise RuntimeError("Context is required for agent authorization")

    async def create_agent(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        name: str,
        instruction: str,
        description: str | None = None,
        icon_url: str | None = None,
        agent_runtime: AgentRuntimeConfig | None = None,
        toolsets: list[AgentToolset] | None = None,
        input_schema: JsonObject | None = None,
        output_schema: JsonObject | None = None,
        visibility: ResourceVisibility | str | None = None,
        metadata: JsonObject | None = None,
        ctx: Context | None = None,
    ) -> Agent:
        await self._require_action(
            requester_user_id=user_id,
            action=Permissions.AGENT_CREATE,
            pod_id=pod_id,
            ctx=ctx,
        )
        normalized_name = name.strip()
        if not normalized_name:
            raise AgentValidationError("Agent name is required")
        if not instruction.strip():
            raise AgentValidationError("Agent instruction is required")
        normalized_visibility = _normalize_agent_visibility(visibility)

        existing = await self.agent_repository.get_by_pod_and_name(
            pod_id=pod_id,
            name=normalized_name,
        )
        if existing is not None:
            raise AgentAlreadyExistsError(normalized_name)

        return await self.agent_repository.create(
            Agent(
                pod_id=pod_id,
                user_id=user_id,
                name=normalized_name,
                description=description,
                icon_url=icon_url,
                instruction=instruction,
                agent_runtime=agent_runtime,
                toolsets=toolsets or [],
                input_schema=input_schema,
                output_schema=output_schema,
                visibility=normalized_visibility,
                metadata=metadata,
            )
        )

    async def list_agents(
        self,
        *,
        pod_id: UUID,
        cursor: UUID | None = None,
        limit: int = 100,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> tuple[list[Agent], UUID | None]:
        if ctx is None:
            raise RuntimeError("Context is required for agent listing")
        await self._require_action(
            requester_user_id=requester_user_id,
            action=Permissions.AGENT_READ,
            pod_id=pod_id,
            ctx=ctx,
        )
        return await self.agent_repository.list_visible_by_pod(
            pod_id=pod_id,
            ctx=ctx,
            cursor=cursor,
            limit=limit,
        )

    async def get_agent_by_name(
        self,
        *,
        pod_id: UUID,
        name: str,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> Agent:
        agent = await self.agent_repository.get_by_pod_and_name(
            pod_id=pod_id,
            name=name,
            ctx=ctx,
        )
        if agent is None:
            raise AgentNotFoundError(name)
        await self._require_action(
            requester_user_id=requester_user_id,
            action=Permissions.AGENT_READ,
            pod_id=pod_id,
            agent_id=agent.id,
            ctx=ctx,
        )
        return agent

    async def update_agent(
        self,
        *,
        pod_id: UUID,
        name: str,
        description: str | None | object = _UNSET,
        icon_url: str | None | object = _UNSET,
        instruction: str | None | object = _UNSET,
        agent_runtime: AgentRuntimeConfig | None | object = _UNSET,
        toolsets: list[AgentToolset] | None | object = _UNSET,
        input_schema: JsonObject | None | object = _UNSET,
        output_schema: JsonObject | None | object = _UNSET,
        visibility: ResourceVisibility | str | None | object = _UNSET,
        metadata: JsonObject | None | object = _UNSET,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> Agent:
        sentinel = _UNSET
        agent = await self.get_agent_by_name(pod_id=pod_id, name=name, ctx=ctx)
        await self._require_action(
            requester_user_id=requester_user_id,
            action=Permissions.AGENT_UPDATE,
            pod_id=pod_id,
            agent_id=agent.id,
            ctx=ctx,
        )

        if description is not sentinel:
            agent.description = description
        if icon_url is not sentinel:
            agent.icon_url = icon_url
        if instruction is not sentinel:
            if instruction is not None and not instruction.strip():
                raise AgentValidationError("Agent instruction is required")
            agent.instruction = instruction
        if agent_runtime is not sentinel:
            agent.agent_runtime = agent_runtime
        if toolsets is not sentinel:
            agent.toolsets = toolsets or []
        if input_schema is not sentinel:
            agent.input_schema = input_schema
        if output_schema is not sentinel:
            agent.output_schema = output_schema
        if visibility is not sentinel:
            agent.visibility = _normalize_agent_visibility(visibility)
        if metadata is not sentinel:
            agent.metadata = metadata

        updated = await self.agent_repository.update(agent)
        if ctx is not None:
            refreshed = await self.agent_repository.get_by_pod_and_name(
                pod_id=pod_id,
                name=name,
                ctx=ctx,
            )
            return refreshed or updated
        return updated

    async def delete_agent(
        self,
        *,
        pod_id: UUID,
        name: str,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> None:
        agent = await self.get_agent_by_name(pod_id=pod_id, name=name, ctx=ctx)
        # A delegated workload must always route through authz — agent.delete is
        # destructive and gated — so the owner shortcut (creator deletes their
        # own agent) never lets a workload bypass it.
        is_delegated = ctx is not None and (
            ctx.actor_type == ActorType.DELEGATED_USER_WORKLOAD
        )
        if requester_user_id is not None and (
            is_delegated or agent.user_id != requester_user_id
        ):
            await self._require_action(
                requester_user_id=requester_user_id,
                action=Permissions.AGENT_DELETE,
                pod_id=pod_id,
                agent_id=agent.id,
                ctx=ctx,
            )
        await self.agent_repository.delete(agent.id)

    def _normalize_names(self, values: list[str], *, label: str) -> list[str]:
        normalized: list[str] = []
        for value in values:
            clean = value.strip()
            if not clean:
                raise AgentValidationError(f"{label} names cannot be empty")
            normalized.append(clean)
        return normalized
