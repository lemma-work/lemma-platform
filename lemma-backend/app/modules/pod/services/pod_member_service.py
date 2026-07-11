"""Pod member service for business logic."""

from __future__ import annotations

from typing import Optional, Sequence, Tuple
from uuid import UUID

from app.modules.identity.contracts import OrganizationRole, normalize_identity_email
from app.modules.pod.domain.errors import (
    PodAccessDeniedError,
    PodConflictError,
    PodMemberNotFoundError,
    PodNotFoundError,
    PodValidationError,
)
from app.modules.pod.domain.pod_entities import PodMemberEntity, PodRole
from app.modules.pod.domain.ports import (
    OrganizationMembershipPort,
    PodMemberRepositoryPort,
    PodRepositoryPort,
)
from app.modules.pod.domain.visibility import (
    normalize_role_list,
    roles_allow_required,
)
from app.modules.pod.services.pod_role_service import PodRoleService
from app.core.log.log import get_logger

logger = get_logger(__name__)


class PodMemberService:
    """Service for PodMember operations."""

    def __init__(
        self,
        pod_member_repository: PodMemberRepositoryPort,
        pod_repository: PodRepositoryPort,
        organization_repository: OrganizationMembershipPort,
        pod_role_service: PodRoleService | None = None,
    ):
        self.pod_member_repository = pod_member_repository
        self.pod_repository = pod_repository
        self.organization_repository = organization_repository
        self.pod_role_service = pod_role_service

    @staticmethod
    def _member_has_role(member: PodMemberEntity | None, role: PodRole) -> bool:
        if member is None:
            return False
        return roles_allow_required(member.roles, role)

    async def assign_member_to_pod(
        self,
        entity: PodMemberEntity,
        requester_user_id: UUID,
    ) -> PodMemberEntity:
        pod = await self.pod_repository.get(entity.pod_id)
        if not pod:
            raise PodNotFoundError()

        requester_org_member = await self.organization_repository.get_member(
            requester_user_id, pod.organization_id
        )

        if not requester_org_member:
            raise PodAccessDeniedError("Requester is not a member of the organization")

        requester_pod_member = await self.pod_member_repository.get_by_pod_and_org_member(
            entity.pod_id, requester_org_member.id
        )
        target_roles = normalize_role_list(entity.roles)
        if not target_roles:
            raise PodValidationError("At least one pod role is required")
        if self.pod_role_service is not None:
            await self.pod_role_service.require_role_manager_bounds(
                pod_id=entity.pod_id,
                requester_user_id=requester_user_id,
                target_roles=target_roles,
                target_user_id=None,
            )
        elif not self._member_has_role(requester_pod_member, PodRole.EDITOR):
            raise PodAccessDeniedError("Only pod editors or admins can assign members")

        existing = await self.pod_member_repository.get_by_pod_and_org_member(
            entity.pod_id, entity.organization_member_id
        )
        if existing:
            raise PodConflictError("Member is already assigned to this pod")

        org_member_added = await self.organization_repository.get_member_by_id(
            entity.organization_member_id
        )
        if not org_member_added:
            raise PodValidationError("Organization member not found")
        if org_member_added.organization_id != pod.organization_id:
            raise PodValidationError(
                "Organization member does not belong to this pod organization"
            )
        entity.user_id = org_member_added.user_id
        if org_member_added.user:
            entity.user = org_member_added.user
            entity.user_email = str(org_member_added.user.email)
            parts = [
                part
                for part in [org_member_added.user.first_name, org_member_added.user.last_name]
                if part
            ]
            entity.user_name = " ".join(parts) or None
        try:
            if org_member_added and org_member_added.user:
                entity.mark_added(
                    user_id=org_member_added.user_id,
                    email=org_member_added.user.email,
                    first_name=org_member_added.user.first_name,
                    last_name=org_member_added.user.last_name,
                )
            else:
                logger.warning(
                    "Could not find user details for org member %s to emit event",
                    entity.organization_member_id,
                )
        except Exception as e:
            logger.error(f"Failed to prepare PodMemberAddedEvent: {e}")

        created = await self.pod_member_repository.create(entity)
        if created.user_id is None:
            created.user_id = entity.user_id
        if created.user_email is None:
            created.user_email = entity.user_email
        if created.user_name is None:
            created.user_name = entity.user_name
        if created.user is None:
            created.user = entity.user
        if self.pod_role_service is not None:
            normalized_roles = await self.pod_role_service.sync_member_roles(
                pod_id=entity.pod_id,
                pod_member_id=created.id,
                roles=target_roles,
                added_by_user_id=requester_user_id,
            )
            created.roles = normalized_roles
        else:
            created.roles = target_roles
        return created

    async def get_pod_member(
        self,
        pod_id: UUID,
        user_id: UUID,
        requester_user_id: UUID,
    ) -> PodMemberEntity:
        return await self.get_pod_member_by_user_id(
            pod_id,
            user_id,
            requester_user_id,
        )

    async def get_pod_member_by_user_id(
        self,
        pod_id: UUID,
        user_id: UUID,
        requester_user_id: UUID,
    ) -> PodMemberEntity:
        pod = await self.pod_repository.get(pod_id)
        if not pod:
            raise PodNotFoundError()

        # Self-membership checks power client auth-guard flows.
        # For own user_id, return 404 when not a member instead of 403.
        if user_id == requester_user_id:
            pod_member = await self.pod_member_repository.get_by_pod_and_user_id(
                pod_id, user_id
            )
            if not pod_member:
                raise PodMemberNotFoundError()
            return pod_member

        requester_org_member = await self.organization_repository.get_member(
            requester_user_id, pod.organization_id
        )
        if not requester_org_member:
            raise PodAccessDeniedError("Requester is not a member of the organization")

        if requester_org_member.role not in [
            OrganizationRole.ORG_OWNER,
            OrganizationRole.ORG_EDITOR,
        ]:
            has_access = await self.pod_member_repository.check_user_has_pod_access(
                pod_id, requester_org_member.id
            )
            if not has_access:
                raise PodAccessDeniedError("Requester doesn't have access to this pod")

        pod_member = await self.pod_member_repository.get_by_pod_and_user_id(
            pod_id, user_id
        )
        if not pod_member:
            raise PodMemberNotFoundError()
        return pod_member

    async def get_pod_member_by_user_email(
        self,
        pod_id: UUID,
        email: str,
        requester_user_id: UUID,
    ) -> PodMemberEntity:
        pod = await self.pod_repository.get(pod_id)
        if not pod:
            raise PodNotFoundError()

        requester_org_member = await self.organization_repository.get_member(
            requester_user_id, pod.organization_id
        )
        if not requester_org_member:
            raise PodAccessDeniedError("Requester is not a member of the organization")

        if requester_org_member.role not in [
            OrganizationRole.ORG_OWNER,
            OrganizationRole.ORG_EDITOR,
        ]:
            has_access = await self.pod_member_repository.check_user_has_pod_access(
                pod_id, requester_org_member.id
            )
            if not has_access:
                raise PodAccessDeniedError("Requester doesn't have access to this pod")

        pod_member = await self.pod_member_repository.get_by_pod_and_user_email(
            pod_id,
            normalize_identity_email(email),
        )
        if not pod_member:
            raise PodMemberNotFoundError()
        return pod_member

    async def get_pod_member_by_id(
        self,
        pod_id: UUID,
        pod_member_id: UUID,
        requester_user_id: UUID,
    ) -> PodMemberEntity:
        pod = await self.pod_repository.get(pod_id)
        if not pod:
            raise PodNotFoundError()

        requester_org_member = await self.organization_repository.get_member(
            requester_user_id, pod.organization_id
        )
        if not requester_org_member:
            raise PodAccessDeniedError("Requester is not a member of the organization")

        if requester_org_member.role not in [
            OrganizationRole.ORG_OWNER,
            OrganizationRole.ORG_EDITOR,
        ]:
            has_access = await self.pod_member_repository.check_user_has_pod_access(
                pod_id, requester_org_member.id
            )
            if not has_access:
                raise PodAccessDeniedError("Requester doesn't have access to this pod")

        pod_member = await self.pod_member_repository.get_by_pod_and_id(
            pod_id,
            pod_member_id,
        )
        if not pod_member:
            raise PodMemberNotFoundError()
        return pod_member

    async def remove_member_from_pod(
        self,
        pod_id: UUID,
        pod_member_id: UUID,
        requester_user_id: UUID,
    ) -> bool:
        pod_member = await self.pod_member_repository.get_by_pod_and_id(
            pod_id, pod_member_id
        )
        if not pod_member:
            raise PodMemberNotFoundError()

        pod = await self.pod_repository.get(pod_member.pod_id)
        if not pod:
            raise PodNotFoundError()

        requester_org_member = await self.organization_repository.get_member(
            requester_user_id, pod.organization_id
        )

        if not requester_org_member:
            raise PodAccessDeniedError("Requester is not a member of the organization")

        if requester_org_member.role != OrganizationRole.ORG_OWNER:
            requester_pod_member = (
                await self.pod_member_repository.get_by_pod_and_org_member(
                    pod_member.pod_id, requester_org_member.id
                )
            )

            if not self._member_has_role(requester_pod_member, PodRole.ADMIN):
                raise PodAccessDeniedError(
                    "Only org owners or pod admins can remove members"
                )

        removed_user_id: UUID | None = None
        try:
            org_member_to_remove = await self.organization_repository.get_member_by_id(
                pod_member.organization_member_id
            )
            if org_member_to_remove:
                removed_user_id = org_member_to_remove.user_id
                pod_member.mark_removed(user_id=org_member_to_remove.user_id)
        except Exception as e:
            logger.warning(f"Failed to fetch user info for event emission: {e}")

        deleted = await self.pod_member_repository.delete_entity(pod_member)
        if not deleted:
            raise PodMemberNotFoundError()

        # Revoke the removed member's cached authorization immediately (and clean
        # up their role assignments / grants). Without this, a cached role
        # snapshot would keep granting pod access until its TTL elapses.
        if self.pod_role_service is not None:
            await self.pod_role_service.revoke_member_authorization(
                pod_id=pod_member.pod_id,
                pod_member_id=pod_member.id,
                user_id=removed_user_id,
            )

        return True

    async def update_member_role(
        self,
        pod_id: UUID,
        pod_member_id: UUID,
        new_role: PodRole,
        requester_user_id: UUID,
    ) -> PodMemberEntity:
        return await self.update_member_roles(
            pod_id=pod_id,
            pod_member_id=pod_member_id,
            roles=[new_role],
            requester_user_id=requester_user_id,
        )

    async def update_member_roles(
        self,
        pod_id: UUID,
        pod_member_id: UUID,
        roles: list[str | PodRole],
        requester_user_id: UUID,
    ) -> PodMemberEntity:
        pod_member = await self.pod_member_repository.get_by_pod_and_id(
            pod_id, pod_member_id
        )
        if not pod_member:
            raise PodMemberNotFoundError()

        pod = await self.pod_repository.get(pod_member.pod_id)
        if not pod:
            raise PodNotFoundError()

        requester_org_member = await self.organization_repository.get_member(
            requester_user_id, pod.organization_id
        )

        if not requester_org_member:
            raise PodAccessDeniedError("Requester is not a member of the organization")

        org_member = await self.organization_repository.get_member_by_id(
            pod_member.organization_member_id
        )
        target_user_id = org_member.user_id if org_member else None
        normalized_roles = normalize_role_list(roles)
        if self.pod_role_service is not None:
            await self.pod_role_service.require_role_manager_bounds(
                pod_id=pod_member.pod_id,
                requester_user_id=requester_user_id,
                target_roles=normalized_roles,
                target_user_id=target_user_id,
            )
        else:
            requester_pod_member = await self.pod_member_repository.get_by_pod_and_org_member(
                pod_member.pod_id, requester_org_member.id
            )
            if not self._member_has_role(requester_pod_member, PodRole.EDITOR):
                raise PodAccessDeniedError(
                    "Only pod editors or admins can update member roles"
                )

        updated = await self.pod_member_repository.update(pod_member)
        if updated.user_id is None:
            updated.user_id = pod_member.user_id
        if updated.user_email is None:
            updated.user_email = pod_member.user_email
        if updated.user_name is None:
            updated.user_name = pod_member.user_name
        if updated.user is None:
            updated.user = pod_member.user
        if self.pod_role_service is not None:
            updated.roles = await self.pod_role_service.sync_member_roles(
                pod_id=pod_member.pod_id,
                pod_member_id=pod_member.id,
                roles=normalized_roles,
                added_by_user_id=requester_user_id,
            )
        else:
            pod_member.assign_roles(normalized_roles)
            updated.roles = normalized_roles
        return updated

    async def list_pod_members(
        self,
        pod_id: UUID,
        requester_user_id: UUID,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[PodMemberEntity], Optional[str]]:
        pod = await self.pod_repository.get(pod_id)
        if not pod:
            raise PodNotFoundError()

        requester_org_member = await self.organization_repository.get_member(
            requester_user_id, pod.organization_id
        )

        if not requester_org_member:
            raise PodAccessDeniedError("Requester is not a member of the organization")

        if requester_org_member.role in [
            OrganizationRole.ORG_OWNER,
            OrganizationRole.ORG_EDITOR,
        ]:
            return await self.pod_member_repository.list_pod_members(
                pod_id, limit, cursor
            )

        has_access = await self.pod_member_repository.check_user_has_pod_access(
            pod_id, requester_org_member.id
        )

        if not has_access:
            raise PodAccessDeniedError("Requester doesn't have access to this pod")

        return await self.pod_member_repository.list_pod_members(pod_id, limit, cursor)

    async def check_pod_permission(
        self,
        user_id: UUID,
        pod_id: UUID,
        required_role: PodRole,
    ) -> bool:
        pod = await self.pod_repository.get(pod_id)
        if not pod:
            return False

        org_member = await self.organization_repository.get_member(
            user_id, pod.organization_id
        )
        if not org_member:
            return False

        if org_member.role in [OrganizationRole.ORG_OWNER, OrganizationRole.ORG_EDITOR]:
            return True

        pod_member = await self.pod_member_repository.get_by_pod_and_org_member(
            pod_id, org_member.id
        )

        if not pod_member:
            return False

        return roles_allow_required(pod_member.roles, required_role)
