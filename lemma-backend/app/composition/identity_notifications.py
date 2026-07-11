"""Identity read adapters used by cross-module notification consumers."""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.identity.infrastructure.organization_repositories import (
    OrganizationRepository,
)
from app.modules.identity.domain.organization_entities import OrganizationRole


def create_user_reader(uow, *, message_bus=None) -> UserRepository:
    return UserRepository(uow, message_bus=message_bus)


async def user_can_view_organization_usage(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    organization_id: UUID,
) -> bool:
    member = await OrganizationRepository(uow).get_member(user_id, organization_id)
    return bool(
        member
        and member.role
        in {OrganizationRole.ORG_OWNER, OrganizationRole.ORG_EDITOR}
    )


async def user_is_organization_member(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    organization_id: UUID,
) -> bool:
    return (
        await OrganizationRepository(uow).get_member(user_id, organization_id)
        is not None
    )


async def resolve_user_email(
    uow: SqlAlchemyUnitOfWork,
    user_id: UUID,
) -> str | None:
    user = await UserRepository(uow).get(user_id)
    email = getattr(user, "email", None) if user is not None else None
    return str(email) if email else None
