from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, or_, select

from app.modules.agent_surfaces.domain.ports import SurfacePodMembershipPort
from app.modules.identity.contracts import UserPreferences
from app.modules.pod.contracts.orm import PodMember
from app.modules.identity.contracts.orm import OrganizationMember, User


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
        return list(result.scalars().all())

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
        except ValidationError:
            return None

    async def clear_user_default_surface_id(self, user_id: UUID, platform: str) -> None:
        user = await self.session.get(User, user_id)
        if user is None:
            return
        try:
            preferences = (
                UserPreferences.model_validate(user.preferences)
                if user.preferences
                else UserPreferences()
            )
        except ValidationError:
            return
        updated = preferences.without_default_surface(platform)
        # Reassign the JSONB value so SQLAlchemy tracks the change; the uow commit
        # flushes it.
        user.preferences = updated.model_dump(mode="json")

    async def set_user_default_surface_id(
        self, user_id: UUID, platform: str, surface_id: UUID
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
        except ValidationError:
            preferences = UserPreferences()
        user.preferences = preferences.with_default_surface(
            platform, surface_id
        ).model_dump(mode="json")

    async def get_pod_member_id(self, user_id: UUID, pod_id: UUID) -> UUID | None:
        stmt = (
            select(PodMember.id)
            .join(
                OrganizationMember,
                OrganizationMember.id == PodMember.organization_member_id,
            )
            .where(
                OrganizationMember.user_id == user_id,
                PodMember.pod_id == pod_id,
            )
        )
        return await self.session.scalar(stmt)

    async def resolve_pod_recipient(
        self, *, pod_id: UUID, reference: str
    ) -> UUID | None:
        candidate = (reference or "").strip()
        if not candidate:
            return None

        members = (
            select(
                PodMember.id.label("pod_member_id"),
                OrganizationMember.user_id.label("user_id"),
            )
            .join(
                OrganizationMember,
                OrganizationMember.id == PodMember.organization_member_id,
            )
            .where(PodMember.pod_id == pod_id)
            .subquery()
        )

        if "@" in candidate:
            # Case-insensitive: an agent will write the address the way a human
            # said it, and mailboxes are not case-sensitive in practice.
            return await self.session.scalar(
                select(members.c.user_id)
                .join(User, User.id == members.c.user_id)
                .where(func.lower(User.email) == candidate.lower())
            )

        try:
            as_uuid = UUID(candidate)
        except ValueError:
            return None
        # A pod member id and a user id are both UUIDs and the caller may not
        # know which it holds. Try member first: it is the pod-scoped one, so a
        # collision (astronomically unlikely) resolves to the safer reading.
        return await self.session.scalar(
            select(members.c.user_id).where(
                or_(
                    members.c.pod_member_id == as_uuid,
                    members.c.user_id == as_uuid,
                )
            )
        )

    async def get_user_display_name(self, user_id: UUID) -> str | None:
        row = (
            await self.session.execute(
                select(User.first_name, User.last_name, User.email).where(
                    User.id == user_id
                )
            )
        ).first()
        if row is None:
            return None
        first_name, last_name, email = row
        name = " ".join(part for part in (first_name, last_name) if part).strip()
        # Falls back to the email rather than to None: the header is omitted
        # for a message to its own asker, and mandatory otherwise, so
        # "on behalf of <someone>" must always have a someone. A member who
        # never set a name is not that exception -- they are the colleague whose
        # authority the line exists to name.
        return name or email
