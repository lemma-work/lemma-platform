"""Stable identity DTOs and ports shared with other modules."""

from typing import Protocol
from uuid import UUID

from app.modules.identity.api.schemas.user_schemas import UserResponse
from app.modules.identity.domain.organization_entities import (
    OrganizationEntity,
    OrganizationMemberEntity,
    OrganizationRole,
    can_grant_org_role,
)
from app.modules.identity.domain.ports import IdentityEmailPort
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.domain.ports import UserRepositoryPort
from app.modules.identity.domain.user_preferences import UserPreferences


class AuthenticatedUser(Protocol):
    id: UUID
    email: str | None


class UserReader(Protocol):
    async def get(self, user_id: UUID) -> AuthenticatedUser | None: ...


__all__ = [
    "AuthenticatedUser",
    "IdentityEmailPort",
    "OrganizationEntity",
    "OrganizationMemberEntity",
    "OrganizationRole",
    "UserEntity",
    "UserReader",
    "UserRepositoryPort",
    "UserResponse",
    "UserPreferences",
    "can_grant_org_role",
    "normalize_identity_email",
]
