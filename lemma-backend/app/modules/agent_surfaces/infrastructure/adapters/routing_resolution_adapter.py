from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.modules.agent_surfaces.domain.ports import SurfacePodMembershipPort
from app.modules.identity.contracts import UserPreferences
from app.composition.surface_identity import OrganizationMember, PodMember, User


class SqlAlchemySurfaceRoutingResolutionAdapter(SurfacePodMembershipPort):
    def __init__(self, uow):
        self.session = uow.session

    async def get_user_pod_ids(self, user_id: UUID) -> list[UUID]:
        stmt = (
            select(PodMember.pod_id)
            .join(
                OrganizationMember,
                OrganizationMember.id == PodMember.organization_member_id,
            )
            .where(OrganizationMember.user_id == user_id)
        )
        result = await self.session.execute(stmt)
        return [pod_id for pod_id in result.scalars().all()]

    async def get_user_email(self, user_id: UUID) -> str | None:
        stmt = select(User.email).where(User.id == user_id)
        return await self.session.scalar(stmt)

    async def get_user_default_surface_id(
        self, user_id: UUID, platform: str
    ) -> UUID | None:
        raw = await self.session.scalar(
            select(User.preferences).where(User.id == user_id)
        )
        if not raw:
            return None
        try:
            return UserPreferences.model_validate(raw).default_surface_for(platform)
        except Exception:
            return None

    async def clear_user_default_surface_id(
        self, user_id: UUID, platform: str
    ) -> None:
        user = await self.session.get(User, user_id)
        if user is None:
            return
        try:
            preferences = (
                UserPreferences.model_validate(user.preferences)
                if user.preferences
                else UserPreferences()
            )
        except Exception:
            return
        updated = preferences.without_default_surface(platform)
        # Reassign the JSONB value so SQLAlchemy tracks the change; the uow commit
        # flushes it.
        user.preferences = updated.model_dump(mode="json")
