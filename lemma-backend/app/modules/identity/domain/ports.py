"""Identity module ports."""

from __future__ import annotations

from typing import Optional, Protocol, Sequence, Tuple, runtime_checkable
from uuid import UUID

from app.modules.identity.domain.organization_entities import (
    OrganizationEntity,
    OrganizationInvitationEntity,
    OrganizationInvitationStatus,
    OrganizationMemberEntity,
    OrganizationRole,
)
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.domain.user_preferences import UserPreferences


class UserRepositoryPort(Protocol):
    async def create(self, entity: UserEntity) -> UserEntity: ...

    async def get(self, id: UUID) -> Optional[UserEntity]: ...

    async def get_by_email(self, email: str) -> Optional[UserEntity]: ...

    async def update(self, entity: UserEntity) -> UserEntity: ...

    async def set_preferences(
        self, user_id: UUID, preferences: UserPreferences
    ) -> UserEntity: ...

    async def get_id_by_mobile_digits(self, digits: str) -> Optional[UUID]: ...

    async def get_ids_by_mobile_numbers(
        self, numbers: list[str], *, verified: bool = True
    ) -> list[UUID]:
        raise NotImplementedError

    async def get_id_by_telegram_lower(self, username_lower: str) -> Optional[UUID]: ...

    async def get_live_id_by_telegram_lower(
        self, username_lower: str
    ) -> Optional[UUID]: ...


class UserCachePort(Protocol):
    async def get(self, user_id: UUID) -> Optional[UserEntity]: ...

    async def set(self, user: UserEntity) -> None: ...

    async def invalidate(self, user_id: UUID) -> None: ...


class OrganizationRepositoryPort(Protocol):
    async def create(self, entity: OrganizationEntity) -> OrganizationEntity: ...

    async def get(self, id: UUID) -> Optional[OrganizationEntity]: ...

    async def get_by_slug(self, slug: str) -> Optional[OrganizationEntity]: ...

    async def update(self, entity: OrganizationEntity) -> OrganizationEntity: ...

    async def get_email_domain_org(
        self, email_domain: str
    ) -> Optional[OrganizationEntity]: ...

    async def list_auto_join_organizations_by_email_domain(
        self,
        email_domain: str,
        user_id: UUID,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[OrganizationEntity], Optional[str]]: ...

    async def get_user_organizations(
        self, user_id: UUID, limit: int = 100, cursor: Optional[str] = None
    ) -> Tuple[Sequence[OrganizationEntity], Optional[str]]: ...

    async def add_member(
        self, entity: OrganizationMemberEntity
    ) -> OrganizationMemberEntity: ...

    async def get_member(
        self, user_id: UUID, organization_id: UUID
    ) -> Optional[OrganizationMemberEntity]: ...

    async def get_member_by_id(
        self, member_id: UUID
    ) -> Optional[OrganizationMemberEntity]: ...

    async def get_member_by_email(
        self, organization_id: UUID, email: str
    ) -> Optional[OrganizationMemberEntity]: ...

    async def list_organization_members(
        self, organization_id: UUID, limit: int = 100, cursor: Optional[str] = None
    ) -> Tuple[Sequence[OrganizationMemberEntity], Optional[str]]: ...

    async def count_members(self, organization_id: UUID) -> int: ...

    async def count_members_with_role_for_update(
        self, organization_id: UUID, role: OrganizationRole
    ) -> int: ...

    async def update_member(
        self, entity: OrganizationMemberEntity
    ) -> OrganizationMemberEntity: ...

    async def delete_member(self, member_id: UUID) -> bool: ...

    async def add_invitation(
        self, entity: OrganizationInvitationEntity
    ) -> OrganizationInvitationEntity: ...

    async def get_invitation_by_id(
        self, invitation_id: UUID
    ) -> Optional[OrganizationInvitationEntity]: ...

    async def get_invitation_by_email(
        self, organization_id: UUID, email: str
    ) -> Optional[OrganizationInvitationEntity]: ...

    async def list_organization_invitations(
        self,
        organization_id: UUID,
        status: OrganizationInvitationStatus | None = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[OrganizationInvitationEntity], Optional[str]]: ...

    async def list_user_invitations(
        self,
        user_email: str,
        status: OrganizationInvitationStatus | None = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[OrganizationInvitationEntity], Optional[str]]: ...

    async def update_invitation(
        self, entity: OrganizationInvitationEntity
    ) -> OrganizationInvitationEntity: ...

    async def delete_invitation(self, invitation_id: UUID) -> bool: ...

    async def delete_invitation_entity(
        self, entity: OrganizationInvitationEntity
    ) -> bool: ...


@runtime_checkable
class IdentityEmailPort(Protocol):
    async def send_invitation_email(
        self,
        *,
        to_email: str,
        organization_name: str,
        inviter_email: str,
        role: OrganizationRole,
        accept_url: str,
        pod_name: str | None = None,
        pod_description: str | None = None,
    ) -> bool: ...

    async def send_signup_welcome_email(
        self,
        *,
        to_email: str,
        first_name: str | None,
    ) -> bool: ...

    async def send_invitation_accepted_email(
        self,
        *,
        to_email: str,
        organization_name: str,
        role: OrganizationRole,
    ) -> bool: ...

    async def send_pod_join_request_email(
        self,
        *,
        to_email: str,
        pod_name: str,
        organization_name: str,
        requester_name: str,
        requester_email: str,
    ) -> bool: ...


class PodMembershipPort(Protocol):
    async def get_pod_organization_id(self, pod_id: UUID) -> Optional[UUID]: ...

    async def get_pod_invitation_details(
        self, pod_id: UUID
    ) -> Optional[tuple[str, str | None, UUID]]: ...

    async def add_member_to_pod(
        self,
        *,
        pod_id: UUID,
        organization_member_id: UUID,
        user_id: UUID,
        user_email: str,
        user_name: Optional[str],
        pod_role: str,
    ) -> None: ...
