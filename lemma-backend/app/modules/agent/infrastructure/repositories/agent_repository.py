"""Agent rows, and which of them a person may see."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.authorization.context import Context, ResourceType, ResourceVisibility
from app.core.authorization.grants import (
    delete_grantee_grants,
    delete_resource_grants,
    delete_resource_sharing_grants,
)
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.events import (
    AgentCreatedEvent,
)
from app.modules.agent.domain.entities import (
    Agent as AgentEntity,
)
from app.modules.agent.infrastructure.models import (
    AgentModel,
)


class AgentRepository:
    """Repository for pod-owned agents."""

    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.uow = uow
        self.session = uow.session

    async def create(self, agent: AgentEntity) -> AgentEntity:
        model = AgentModel(
            id=agent.id,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
            pod_id=agent.pod_id,
            user_id=agent.user_id,
            name=agent.name,
            description=agent.description,
            icon_url=agent.icon_url,
            visibility=agent.visibility,
            instruction=agent.instruction,
            agent_runtime=(
                agent.agent_runtime.model_dump(mode="json")
                if agent.agent_runtime
                else None
            ),
            toolsets=[toolset.value for toolset in agent.toolsets],
            input_schema=agent.input_schema,
            output_schema=agent.output_schema,
            agent_metadata=agent.metadata,
        )
        self.session.add(model)
        await self.session.flush()
        # The single write path behind every creation route.
        self.uow.collect_events(
            [
                AgentCreatedEvent(
                    agent_id=model.id,
                    pod_id=model.pod_id,
                    user_id=model.user_id,
                    tool_count=len(agent.toolsets or ()),
                )
            ]
        )
        return model.to_entity()

    def _to_entity_with_allowed_actions(
        self,
        model: AgentModel,
        allowed_actions: list[str] | tuple[str, ...] | None = None,
    ) -> AgentEntity:
        entity = model.to_entity()
        if allowed_actions is not None:
            entity.allowed_actions = list(allowed_actions)
        return entity

    async def get(
        self, agent_id: UUID, ctx: Context | None = None
    ) -> AgentEntity | None:
        if ctx is None:
            result = await self.session.execute(
                select(AgentModel).where(AgentModel.id == agent_id)
            )
            model = result.scalar_one_or_none()
            return model.to_entity() if model else None
        actions = allowed_actions_expr(
            ctx=ctx,
            resource_type=ResourceType.AGENT,
            resource_id_col=AgentModel.id,
            pod_id_col=AgentModel.pod_id,
            owner_user_id_col=AgentModel.user_id,
            visibility_col=AgentModel.visibility,
        )
        result = await self.session.execute(
            select(AgentModel, actions).where(AgentModel.id == agent_id)
        )
        row = result.one_or_none()
        return self._to_entity_with_allowed_actions(row[0], row[1]) if row else None

    async def update(self, agent: AgentEntity) -> AgentEntity:
        result = await self.session.execute(
            select(AgentModel).where(AgentModel.id == agent.id)
        )
        model = result.scalar_one_or_none()
        if model is None:
            return agent

        model.name = agent.name
        model.description = agent.description
        model.icon_url = agent.icon_url
        previous_visibility = model.visibility
        model.visibility = agent.visibility
        if (
            previous_visibility == ResourceVisibility.RESTRICTED.value
            and agent.visibility != ResourceVisibility.RESTRICTED.value
        ):
            await delete_resource_sharing_grants(
                self.session,
                pod_id=agent.pod_id,
                resource_type=ResourceType.AGENT,
                resource_id=agent.id,
            )
        model.instruction = agent.instruction
        model.agent_runtime = (
            agent.agent_runtime.model_dump(mode="json") if agent.agent_runtime else None
        )
        model.toolsets = [toolset.value for toolset in agent.toolsets]
        model.input_schema = agent.input_schema
        model.output_schema = agent.output_schema
        model.agent_metadata = agent.metadata
        await self.session.flush()
        return model.to_entity()

    async def delete(self, agent_id: UUID) -> None:
        result = await self.session.execute(
            select(AgentModel).where(AgentModel.id == agent_id)
        )
        model = result.scalar_one_or_none()
        if model is not None:
            if model.pod_id is not None:
                await delete_resource_grants(
                    self.session,
                    pod_id=model.pod_id,
                    resource_type=ResourceType.AGENT,
                    resource_id=agent_id,
                )
                await delete_grantee_grants(
                    self.session,
                    pod_id=model.pod_id,
                    grantee_type="AGENT",
                    grantee_id=agent_id,
                )
            await self.session.delete(model)
            await self.session.flush()

    async def list_by_pod(
        self,
        *,
        pod_id: UUID,
        cursor: UUID | None = None,
        limit: int = 100,
    ) -> tuple[list[AgentEntity], UUID | None]:
        stmt = select(AgentModel).where(AgentModel.pod_id == pod_id)
        if cursor is not None:
            stmt = stmt.where(AgentModel.id < cursor)
        stmt = stmt.order_by(AgentModel.id.desc()).limit(limit + 1)
        result = await self.session.execute(stmt)
        rows = list(result.scalars())
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        next_cursor = rows[-1].id if has_more and rows else None
        return [row.to_entity() for row in rows], next_cursor

    async def list_visible_by_pod(
        self,
        *,
        pod_id: UUID,
        ctx: Context,
        cursor: UUID | None = None,
        limit: int = 100,
    ) -> tuple[list[AgentEntity], UUID | None]:
        actions = allowed_actions_expr(
            ctx=ctx,
            resource_type=ResourceType.AGENT,
            resource_id_col=AgentModel.id,
            pod_id_col=AgentModel.pod_id,
            owner_user_id_col=AgentModel.user_id,
            visibility_col=AgentModel.visibility,
        )
        stmt = select(AgentModel, actions).where(
            AgentModel.pod_id == pod_id,
            allowed_actions_contains(actions, Permissions.AGENT_READ),
        )
        if cursor is not None:
            stmt = stmt.where(AgentModel.id < cursor)
        stmt = stmt.order_by(AgentModel.id.desc()).limit(limit + 1)
        result = await self.session.execute(stmt)
        rows = list(result.all())
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        next_cursor = rows[-1][0].id if has_more and rows else None
        return [
            self._to_entity_with_allowed_actions(model, actions)
            for model, actions in rows
        ], next_cursor

    async def get_by_pod_and_name(
        self, *, pod_id: UUID, name: str, ctx: Context | None = None
    ) -> AgentEntity | None:
        if ctx is None:
            result = await self.session.execute(
                select(AgentModel).where(
                    AgentModel.pod_id == pod_id, AgentModel.name == name
                )
            )
            model = result.scalar_one_or_none()
            return model.to_entity() if model else None
        actions = allowed_actions_expr(
            ctx=ctx,
            resource_type=ResourceType.AGENT,
            resource_id_col=AgentModel.id,
            pod_id_col=AgentModel.pod_id,
            owner_user_id_col=AgentModel.user_id,
            visibility_col=AgentModel.visibility,
        )
        result = await self.session.execute(
            select(AgentModel, actions).where(
                AgentModel.pod_id == pod_id, AgentModel.name == name
            )
        )
        row = result.one_or_none()
        return self._to_entity_with_allowed_actions(row[0], row[1]) if row else None
