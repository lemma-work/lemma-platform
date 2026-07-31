"""Reads behind AgentContextBriefBuilder (pod name, user email, agent grants).

Keeps the brief builder SQLAlchemy-free; it aggregates read-only display data
across pod, identity, and core authorization, so the raw queries live here.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.authorization.context import ResourceType
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.resource_names import resolve_resource_names_by_ids
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.composition.agent_context_models import Pod, User


class AgentContextBriefRepository:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._session = uow.session

    async def get_pod_name(self, pod_id: UUID) -> str | None:
        return (
            await self._session.execute(select(Pod.name).where(Pod.id == pod_id))
        ).scalar_one_or_none()

    async def get_user_email(self, user_id: UUID) -> str | None:
        user = (
            await self._session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        return user.email if user is not None else None

    async def get_agent_grants(
        self, *, pod_id: UUID, agent_id: UUID
    ) -> list[tuple[str, UUID, str]]:
        """(resource_type, resource_id, permission_id) granted to an agent."""
        rows = (
            await self._session.execute(
                select(
                    ResourcePermissionGrantModel.resource_type,
                    ResourcePermissionGrantModel.resource_id,
                    ResourcePermissionGrantModel.permission_id,
                ).where(
                    ResourcePermissionGrantModel.pod_id == pod_id,
                    ResourcePermissionGrantModel.grantee_type == "AGENT",
                    ResourcePermissionGrantModel.grantee_id == agent_id,
                )
            )
        ).all()
        return [(rt, rid, pid) for rt, rid, pid in rows]

    async def resolve_resource_names(
        self, *, pod_id: UUID, refs: list[tuple[ResourceType, UUID]]
    ) -> dict:
        return await resolve_resource_names_by_ids(
            self._session, pod_id=pod_id, refs=refs
        )
