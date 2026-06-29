from __future__ import annotations

from secrets import token_hex
from uuid import UUID
from typing import Optional

from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.permissions import Permissions
from app.modules.identity.domain.organization_entities import OrganizationRole
from app.modules.icon.services.icon_service import IconService
from app.modules.pod.domain.errors import (
    PodAccessDeniedError,
    PodNotFoundError,
)
from app.modules.pod.domain.pod_entities import (
    PodEntity,
    PodMemberEntity,
    PodRole,
    PodUpdateEntity,
)
from app.modules.pod.domain.ports import (
    OrganizationMembershipPort,
    PodMemberRepositoryPort,
    PodRepositoryPort,
)
from app.modules.pod.domain.visibility import roles_allow_required
from app.modules.pod.services.pod_role_service import PodRoleService


class PodService:
    def __init__(
        self,
        pod_repository: PodRepositoryPort,
        pod_member_repository: PodMemberRepositoryPort,
        organization_repository: OrganizationMembershipPort,
        pod_role_service: PodRoleService | None = None,
        authorization_service: object | None = None,
        icon_service: IconService | None = None,
    ):
        self.pod_repository = pod_repository
        self.pod_member_repository = pod_member_repository
        self.organization_repository = organization_repository
        self.pod_role_service = pod_role_service
        self.authorization_service = authorization_service
        self.icon_service = icon_service

    async def create_pod(self, entity: PodEntity, creator_user_id: UUID) -> PodEntity:
        member = await self.organization_repository.get_member(
            creator_user_id, entity.organization_id
        )
        if not member:
            raise PodAccessDeniedError(
                "User must be a member of the organization to create a pod"
            )

        # Aggregate method registers pod.created event.
        entity.mark_created(creator_user_id)

        pod = await self.pod_repository.create(entity)

        pod_member = PodMemberEntity(
            pod_id=pod.id,
            organization_member_id=member.id,
            roles=[PodRole.ADMIN.value],
        )
        created_member = await self.pod_member_repository.create(pod_member)
        if self.pod_role_service is not None:
            await self.pod_role_service.sync_member_roles(
                pod_id=pod.id,
                pod_member_id=created_member.id,
                roles=[PodRole.ADMIN],
                added_by_user_id=creator_user_id,
            )

        return pod

    async def get_pod(
        self, pod_id: UUID, requester_user_id: UUID
    ) -> Optional[PodEntity]:
        pod = await self.pod_repository.get(pod_id)
        if not pod:
            return None

        org_member = await self.organization_repository.get_member(
            requester_user_id, pod.organization_id
        )
        if not org_member:
            raise PodAccessDeniedError("User doesn't have access to this pod")

        if org_member.role in [OrganizationRole.ORG_OWNER, OrganizationRole.ORG_EDITOR]:
            return pod

        has_access = await self.pod_member_repository.check_user_has_pod_access(
            pod_id, org_member.id
        )
        if not has_access:
            raise PodAccessDeniedError("User doesn't have access to this pod")

        return pod

    async def update_pod(
        self,
        pod_id: UUID,
        data: PodUpdateEntity,
        requester_user_id: UUID,
        ctx: Context | None = None,
    ) -> PodEntity:
        pod_entity = await self.pod_repository.get(pod_id)
        if not pod_entity:
            raise PodNotFoundError()

        if ctx is None:
            raise PodAccessDeniedError("Context is required for pod authorization")
        await ctx.require(Permissions.POD_UPDATE, ResourceRef.pod(pod_id, pod_entity.organization_id))

        merged_dict = pod_entity.model_dump()
        update_data = data.model_dump(exclude_unset=True)
        # Config is a typed multi-field blob; merge field-wise so a partial
        # update (e.g. only default_runtime, or only join_policy) preserves
        # the other fields instead of resetting them to their defaults.
        if update_data.get("config") is not None:
            merged_config = merged_dict.get("config") or {}
            merged_config.update(update_data["config"])
            # Keep the legacy provider-only key in sync with the full runtime so
            # any code still reading ``default_profile_id`` stays correct.
            default_runtime = merged_config.get("default_runtime")
            if isinstance(default_runtime, dict) and default_runtime.get("profile_id"):
                merged_config["default_profile_id"] = default_runtime["profile_id"]
            update_data["config"] = merged_config
        merged_dict.update(update_data)

        updated_entity = PodEntity(**merged_dict)
        updated = await self.pod_repository.update(updated_entity)

        if (
            self.icon_service
            and "icon_url" in update_data
            and pod_entity.icon_url != updated.icon_url
        ):
            await self.icon_service.delete_by_url(pod_entity.icon_url)

        return updated

    async def delete_pod(self, pod_id: UUID, requester_user_id: UUID) -> bool:
        pod = await self.pod_repository.get(pod_id)
        if not pod:
            raise PodNotFoundError()

        org_member = await self.organization_repository.get_member(
            requester_user_id, pod.organization_id
        )
        if not org_member:
            raise PodAccessDeniedError()

        if org_member.role != OrganizationRole.ORG_OWNER:
            pod_member = await self.pod_member_repository.get_by_pod_and_org_member(
                pod_id, org_member.id
            )
            if not pod_member or not roles_allow_required(
                pod_member.roles,
                PodRole.ADMIN,
            ):
                raise PodAccessDeniedError("Permission denied")

        old_icon_url = pod.icon_url
        pod.name = self._build_deleted_pod_name(pod.name)
        pod.mark_deleted()
        await self.pod_repository.update(pod)
        if self.icon_service:
            await self.icon_service.delete_by_url(old_icon_url)
        return True

    async def list_pods_by_organization(
        self, organization_id: UUID, requester_user_id: UUID, limit: int = 100, cursor: str | None = None
    ):
        org_member = await self.organization_repository.get_member(
            requester_user_id, organization_id
        )
        if not org_member:
            raise PodAccessDeniedError()
        if org_member.role == OrganizationRole.ORG_OWNER:
            return await self.pod_repository.list_by_org(organization_id, limit, cursor)
        return await self.pod_repository.list_by_org_member(
            organization_id,
            org_member.id,
            limit,
            cursor,
        )

    def _build_deleted_pod_name(self, name: str) -> str:
        suffix = token_hex(4)
        deleted_name = f"deleted-{suffix}-{name}"
        return deleted_name[:255]
