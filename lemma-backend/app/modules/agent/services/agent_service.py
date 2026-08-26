"""Application service for pod-owned agents."""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.domain.sentinels import UNSET, UnsetType
from app.core.authorization.context import (
    ActorType,
    Context,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.delegation import POD_DEFAULT_AGENT_SELECTOR_ALIASES
from app.core.authorization.delegation_revocation import revoke_delegation
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
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
        uow: SqlAlchemyUnitOfWork,
        agent_repository: AgentRepository,
        authorization_service: object,
    ):
        self.uow = uow
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
        if normalized_name in POD_DEFAULT_AGENT_SELECTOR_ALIASES:
            raise AgentValidationError(
                f"Agent name {normalized_name!r} is reserved for the pod-default assistant"
            )
        if not instruction.strip():
            raise AgentValidationError("Agent instruction is required")
        normalized_visibility = _normalize_agent_visibility(visibility)

        existing = await self.agent_repository.get_by_pod_and_name(
            pod_id=pod_id,
            name=normalized_name,
        )
        if existing is not None:
            raise AgentAlreadyExistsError(normalized_name)

        agent = await self.agent_repository.create(
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

        # Give it a mailbox so the UI can offer "email this agent at …" from the
        # moment it exists. Best-effort by design: a deployment with no mail
        # domain still gets a perfectly good agent, just not an emailable one.
        from app.composition.agent_email_surface import provision_agent_email_surface

        await provision_agent_email_surface(
            self.uow,
            pod_id=pod_id,
            agent_id=agent.id,
            agent_name=agent.name,
        )
        return agent

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
        description: str | None | UnsetType = UNSET,
        icon_url: str | None | UnsetType = UNSET,
        instruction: str | None | UnsetType = UNSET,
        agent_runtime: AgentRuntimeConfig | None | UnsetType = UNSET,
        toolsets: list[AgentToolset] | None | UnsetType = UNSET,
        input_schema: JsonObject | None | UnsetType = UNSET,
        output_schema: JsonObject | None | UnsetType = UNSET,
        visibility: ResourceVisibility | str | None | UnsetType = UNSET,
        metadata: JsonObject | None | UnsetType = UNSET,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> Agent:
        agent = await self.get_agent_by_name(pod_id=pod_id, name=name, ctx=ctx)
        await self._require_action(
            requester_user_id=requester_user_id,
            action=Permissions.AGENT_UPDATE,
            pod_id=pod_id,
            agent_id=agent.id,
            ctx=ctx,
        )

        # `isinstance` rather than `is not UNSET`, matching the other PATCH
        # services here: an identity test against the singleton reads the same
        # but narrows nothing, so every assignment below stayed `| UnsetType`.
        if not isinstance(description, UnsetType):
            agent.description = description
        if not isinstance(icon_url, UnsetType):
            agent.icon_url = icon_url
        if not isinstance(instruction, UnsetType):
            # `None` is rejected alongside blank, not accepted: the entity's
            # instruction is a `str`, so clearing it wrote a null into a field
            # that has no null.
            if instruction is None or not instruction.strip():
                raise AgentValidationError("Agent instruction is required")
            agent.instruction = instruction
        if not isinstance(agent_runtime, UnsetType):
            agent.agent_runtime = agent_runtime
        if not isinstance(toolsets, UnsetType):
            agent.toolsets = toolsets or []
        if not isinstance(input_schema, UnsetType):
            agent.input_schema = input_schema
        if not isinstance(output_schema, UnsetType):
            agent.output_schema = output_schema
        if not isinstance(visibility, UnsetType):
            agent.visibility = _normalize_agent_visibility(visibility)
        if not isinstance(metadata, UnsetType):
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
        # Before the row goes, not after: `agent_surfaces.agent_id` is
        # ON DELETE SET NULL, so once the agent is deleted its surfaces are no
        # longer identifiable as its own — they read as the pod assistant's,
        # and the pod starts answering from a deleted agent's address.
        from app.composition.agent_email_surface import teardown_agent_surfaces

        await teardown_agent_surfaces(self.uow, pod_id=pod_id, agent_id=agent.id)
        await self.agent_repository.delete(agent.id)
        # Revoke any in-flight delegated token minted for this agent so it stops
        # working immediately rather than lingering until the token expires.
        await revoke_delegation(actor_id=agent.id)

    def _normalize_names(self, values: list[str], *, label: str) -> list[str]:
        normalized: list[str] = []
        for value in values:
            clean = value.strip()
            if not clean:
                raise AgentValidationError(f"{label} names cannot be empty")
            normalized.append(clean)
        return normalized
