from __future__ import annotations

from typing import Optional, Sequence, Tuple
from uuid import UUID

from app.modules.identity.domain.organization_entities import (
    OrganizationMemberEntity,
    OrganizationRole,
)
from app.modules.pod.domain.errors import (
    PodAccessDeniedError,
    PodConflictError,
    PodJoinRequestNotFoundError,
    PodNotFoundError,
)
from app.modules.pod.domain.pod_entities import (
    PodJoinPolicy,
    PodJoinRequestEntity,
    PodJoinRequestStatus,
    PodMemberEntity,
    PodRole,
)
from app.modules.pod.domain.ports import (
    OrganizationMembershipPort,
    PodJoinRequestRepositoryPort,
    PodMemberRepositoryPort,
    PodRepositoryPort,
)
from app.modules.pod.services.pod_role_service import PodRoleService
from app.modules.pod.domain.visibility import roles_allow_required


class PodJoinRequestService:
    def __init__(
        self,
        pod_join_request_repository: PodJoinRequestRepositoryPort,
        pod_member_repository: PodMemberRepositoryPort,
        pod_repository: PodRepositoryPort,
        organization_repository: OrganizationMembershipPort,
        pod_role_service: PodRoleService | None = None,
    ):
        self.pod_join_request_repository = pod_join_request_repository
        self.pod_member_repository = pod_member_repository
        self.pod_repository = pod_repository
        self.organization_repository = organization_repository
        self.pod_role_service = pod_role_service

    async def _require_manage_permission(
        self,
        *,
        pod_id: UUID,
        organization_id: UUID,
        requester_user_id: UUID,
    ) -> OrganizationMemberEntity:
        requester_org_member = await self.organization_repository.get_member(
            requester_user_id, organization_id
        )
        if not requester_org_member:
            raise PodAccessDeniedError("Requester is not a member of the organization")

        if requester_org_member.role in [
            OrganizationRole.ORG_OWNER,
            OrganizationRole.ORG_EDITOR,
        ]:
            return requester_org_member

        requester_pod_member = await self.pod_member_repository.get_by_pod_and_org_member(
            pod_id, requester_org_member.id
        )
        if not requester_pod_member or not roles_allow_required(
            requester_pod_member.roles,
            PodRole.ADMIN,
        ):
            raise PodAccessDeniedError(
                "Only org owners/editors or pod admins can manage join requests"
            )

        return requester_org_member

    def _policy_allows_self_join(
        self,
        join_policy: PodJoinPolicy,
        org_member: Optional[OrganizationMemberEntity],
    ) -> bool:
        """Whether the pod's join policy already entitles this user to join.

        PUBLIC pods admit anyone; ORG_MEMBERS pods admit existing org members.
        INVITE_ONLY never self-admits.
        """
        if join_policy == PodJoinPolicy.PUBLIC:
            return True
        if join_policy == PodJoinPolicy.ORG_MEMBERS:
            return org_member is not None
        return False

    async def _ensure_org_membership_for_join(
        self,
        *,
        pod,
        requester_user_id: UUID,
        org_member: Optional[OrganizationMemberEntity],
    ) -> Tuple[OrganizationMemberEntity, Optional[OrganizationMemberEntity]]:
        """Resolve the requester's org membership per the pod's join policy.

        Returns ``(org_member, created_org_member)`` where ``created_org_member``
        is non-None only when a new org membership was created (PUBLIC pods), so
        the caller can sync org-level authorization. Raises when the policy
        forbids a self-join for this user.
        """
        join_policy = pod.config.join_policy
        created_org_member: Optional[OrganizationMemberEntity] = None
        if join_policy == PodJoinPolicy.PUBLIC:
            if org_member is None:
                org_member = await self.organization_repository.add_member(
                    OrganizationMemberEntity(
                        user_id=requester_user_id,
                        organization_id=pod.organization_id,
                        role=OrganizationRole.ORG_MEMBER,
                    )
                )
                created_org_member = org_member
        elif join_policy == PodJoinPolicy.ORG_MEMBERS:
            if org_member is None:
                raise PodAccessDeniedError(
                    "Only organization members can join this pod"
                )
        else:  # INVITE_ONLY
            raise PodAccessDeniedError(
                "This pod is invite-only; request to join instead"
            )
        return org_member, created_org_member

    async def _ensure_pod_membership(
        self,
        *,
        pod_id: UUID,
        org_member: OrganizationMemberEntity,
        requester_user_id: UUID,
    ) -> PodMemberEntity:
        """Idempotently add the org member to the pod with the base USER role."""
        existing_pod_member = await self.pod_member_repository.get_by_pod_and_org_member(
            pod_id, org_member.id
        )
        if existing_pod_member:
            return existing_pod_member

        pod_member = PodMemberEntity(
            pod_id=pod_id,
            organization_member_id=org_member.id,
            roles=[PodRole.USER.value],
        )
        created = await self.pod_member_repository.create(pod_member)
        if self.pod_role_service is not None:
            created.roles = await self.pod_role_service.sync_member_roles(
                pod_id=pod_id,
                pod_member_id=created.id,
                roles=[PodRole.USER],
                added_by_user_id=requester_user_id,
            )
        else:
            created.roles = [PodRole.USER.value]
        return created

    async def request_join(
        self,
        pod_id: UUID,
        requester_user_id: UUID,
    ) -> Tuple[PodJoinRequestEntity, Optional[OrganizationMemberEntity]]:
        """Request to join a pod, or self-join immediately when policy allows.

        When the pod's join policy already entitles the requester to join
        (PUBLIC, or ORG_MEMBERS for an existing org member), the membership is
        created right away and an ``APPROVED`` request is returned — skipping the
        pending/approval round-trip. Otherwise a ``PENDING`` request is created.

        Returns the request plus the org member that was *created* as part of an
        auto-join (``None`` otherwise), so the caller can sync org-level
        authorization for the new member.
        """
        pod = await self.pod_repository.get(pod_id)
        if not pod:
            raise PodNotFoundError()

        org_member = await self.organization_repository.get_member(
            requester_user_id, pod.organization_id
        )
        if org_member:
            if org_member.role == OrganizationRole.ORG_OWNER:
                raise PodConflictError(
                    "Org owner has access to all pods by default"
                )

            existing_pod_member = await self.pod_member_repository.get_by_pod_and_org_member(
                pod_id, org_member.id
            )
            if existing_pod_member:
                raise PodConflictError("User is already a member of this pod")

        if self._policy_allows_self_join(pod.config.join_policy, org_member):
            org_member, created_org_member = await self._ensure_org_membership_for_join(
                pod=pod,
                requester_user_id=requester_user_id,
                org_member=org_member,
            )
            await self._ensure_pod_membership(
                pod_id=pod_id,
                org_member=org_member,
                requester_user_id=requester_user_id,
            )

            # Reuse a prior pending request (e.g. created while the pod was
            # invite-only) instead of leaving it dangling.
            existing_pending = await self.pod_join_request_repository.get_pending_by_pod_and_user(
                pod_id,
                requester_user_id,
            )
            join_request = existing_pending or PodJoinRequestEntity(
                pod_id=pod_id,
                organization_id=pod.organization_id,
                user_id=requester_user_id,
            )
            join_request.mark_approved(
                approved_by_user_id=requester_user_id,
                org_role=org_member.role,
                pod_role=PodRole.USER,
            )
            if existing_pending is not None:
                created_request = await self.pod_join_request_repository.update(
                    join_request
                )
            else:
                created_request = await self.pod_join_request_repository.create(
                    join_request
                )
            return created_request, created_org_member

        existing_pending = await self.pod_join_request_repository.get_pending_by_pod_and_user(
            pod_id,
            requester_user_id,
        )
        if existing_pending:
            return existing_pending, None

        entity = PodJoinRequestEntity(
            pod_id=pod_id,
            organization_id=pod.organization_id,
            user_id=requester_user_id,
            status=PodJoinRequestStatus.PENDING,
        )
        entity.mark_requested()
        created = await self.pod_join_request_repository.create(entity)
        return created, None

    async def join_pod(
        self,
        pod_id: UUID,
        requester_user_id: UUID,
    ) -> Tuple[PodMemberEntity, Optional[OrganizationMemberEntity]]:
        """Self-join a pod when its join policy allows it.

        Returns the pod membership plus the org member that was *created* as part
        of joining (``None`` if the user was already an org member), so the caller
        can sync org-level authorization for the new member.
        """
        pod = await self.pod_repository.get(pod_id)
        if not pod:
            raise PodNotFoundError()

        org_member = await self.organization_repository.get_member(
            requester_user_id, pod.organization_id
        )

        if org_member and org_member.role == OrganizationRole.ORG_OWNER:
            raise PodConflictError("Org owner has access to all pods by default")

        org_member, created_org_member = await self._ensure_org_membership_for_join(
            pod=pod,
            requester_user_id=requester_user_id,
            org_member=org_member,
        )
        created = await self._ensure_pod_membership(
            pod_id=pod_id,
            org_member=org_member,
            requester_user_id=requester_user_id,
        )
        return created, created_org_member

    async def get_my_join_request(
        self,
        pod_id: UUID,
        requester_user_id: UUID,
    ) -> Optional[PodJoinRequestEntity]:
        pod = await self.pod_repository.get(pod_id)
        if not pod:
            raise PodNotFoundError()

        return await self.pod_join_request_repository.get_pending_by_pod_and_user(
            pod_id,
            requester_user_id,
        )

    async def list_join_requests(
        self,
        pod_id: UUID,
        requester_user_id: UUID,
        *,
        status: PodJoinRequestStatus | None = PodJoinRequestStatus.PENDING,
        limit: int = 100,
        cursor: str | None = None,
    ) -> Tuple[Sequence[PodJoinRequestEntity], Optional[str]]:
        pod = await self.pod_repository.get(pod_id)
        if not pod:
            raise PodNotFoundError()

        await self._require_manage_permission(
            pod_id=pod.id,
            organization_id=pod.organization_id,
            requester_user_id=requester_user_id,
        )

        return await self.pod_join_request_repository.list_by_pod(
            pod_id,
            status=status,
            limit=limit,
            cursor=cursor,
        )

    async def approve_join_request(
        self,
        pod_id: UUID,
        join_request_id: UUID,
        requester_user_id: UUID,
        *,
        org_role: OrganizationRole = OrganizationRole.ORG_MEMBER,
        pod_role: PodRole = PodRole.USER,
    ) -> PodJoinRequestEntity:
        pod = await self.pod_repository.get(pod_id)
        if not pod:
            raise PodNotFoundError()

        await self._require_manage_permission(
            pod_id=pod.id,
            organization_id=pod.organization_id,
            requester_user_id=requester_user_id,
        )

        join_request = await self.pod_join_request_repository.get(join_request_id)
        if not join_request or join_request.pod_id != pod_id:
            raise PodJoinRequestNotFoundError()

        if join_request.status != PodJoinRequestStatus.PENDING:
            raise PodConflictError(
                f"Join request is already {join_request.status.value.lower()}"
            )

        target_org_member = await self.organization_repository.get_member(
            join_request.user_id,
            pod.organization_id,
        )
        if not target_org_member:
            target_org_member = await self.organization_repository.add_member(
                OrganizationMemberEntity(
                    user_id=join_request.user_id,
                    organization_id=pod.organization_id,
                    role=org_role,
                )
            )

        existing_pod_member = await self.pod_member_repository.get_by_pod_and_org_member(
            pod_id,
            target_org_member.id,
        )
        if not existing_pod_member:
            pod_member = PodMemberEntity(
                pod_id=pod_id,
                organization_member_id=target_org_member.id,
                roles=[pod_role.value],
            )
            created_member = await self.pod_member_repository.create(pod_member)
            if self.pod_role_service is not None:
                await self.pod_role_service.sync_member_roles(
                    pod_id=pod_id,
                    pod_member_id=created_member.id,
                    roles=[pod_role],
                    added_by_user_id=requester_user_id,
                )

        join_request.mark_approved(
            approved_by_user_id=requester_user_id,
            org_role=target_org_member.role,
            pod_role=pod_role,
        )
        return await self.pod_join_request_repository.update(join_request)
