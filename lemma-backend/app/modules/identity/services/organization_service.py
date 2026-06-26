from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence, Tuple
from uuid import UUID

from app.core.helpers.slug import slugify
from app.modules.identity.domain.errors import (
    IdentityAccessDeniedError,
    IdentityValidationError,
    OrganizationConflictError,
    OrganizationInvitationNotFoundError,
    OrganizationMemberNotFoundError,
    OrganizationNotFoundError,
    UserNotFoundError,
)
from app.modules.identity.domain.organization_entities import (
    OrganizationEntity,
    OrganizationInvitationEntity,
    OrganizationInvitationStatus,
    OrganizationJoinPolicy,
    OrganizationMemberEntity,
    OrganizationRole,
)
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.domain.ports import (
    OrganizationRepositoryPort,
    PodMembershipPort,
    UserRepositoryPort,
)


class OrganizationService:
    PERSONAL_EMAIL_DOMAINS = {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "msn.com",
    }

    def __init__(
        self,
        organization_repository: OrganizationRepositoryPort,
        user_repository: UserRepositoryPort,
        invitation_accept_base_url: str,
        pod_membership_port: PodMembershipPort | None = None,
    ):
        self.organization_repository = organization_repository
        self.user_repository = user_repository
        self.invitation_accept_base_url = invitation_accept_base_url.rstrip("/")
        self.pod_membership_port = pod_membership_port

    def _build_invitation_accept_url(self, invitation_id: UUID) -> str:
        return f"{self.invitation_accept_base_url}/invitations/{invitation_id}/accept"

    def _email_domain(self, email: str) -> str:
        return email.rsplit("@", 1)[-1].strip().lower()

    def _normalizable_email_domain(self, email: str) -> str | None:
        domain = self._email_domain(email)
        if not domain or domain in self.PERSONAL_EMAIL_DOMAINS:
            return None
        return domain

    async def _mark_invitation_expired_if_needed(
        self, invitation: OrganizationInvitationEntity
    ) -> OrganizationInvitationEntity:
        if invitation.is_expired():
            invitation.mark_expired(datetime.now(timezone.utc))
            return await self.organization_repository.update_invitation(invitation)
        return invitation

    async def _enrich_invitation_display_fields(
        self, invitation: OrganizationInvitationEntity
    ) -> OrganizationInvitationEntity:
        organization = await self.organization_repository.get(invitation.organization_id)
        if organization:
            invitation.organization_name = organization.name

        if invitation.pod_id is not None and self.pod_membership_port is not None:
            pod_details = await self.pod_membership_port.get_pod_invitation_details(
                invitation.pod_id
            )
            if pod_details:
                invitation.pod_name = pod_details[0]
                invitation.pod_description = pod_details[1]

        return invitation

    async def _enrich_invitation_list_display_fields(
        self, invitations: Sequence[OrganizationInvitationEntity]
    ) -> list[OrganizationInvitationEntity]:
        return [
            await self._enrich_invitation_display_fields(invitation)
            for invitation in invitations
        ]

    async def _require_member(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        allowed_roles: Sequence[OrganizationRole] | None = None,
        denied_message: str,
    ) -> OrganizationMemberEntity:
        member = await self.organization_repository.get_member(user_id, organization_id)
        if not member:
            raise IdentityAccessDeniedError(denied_message)

        if allowed_roles and member.role not in allowed_roles:
            raise IdentityAccessDeniedError(denied_message)

        return member

    async def _resolve_email_domain_for_policy(
        self,
        *,
        owner: UserEntity,
        join_policy: OrganizationJoinPolicy,
        provided_domain: str | None,
        exclude_org_id: UUID | None = None,
    ) -> str | None:
        """Resolve the domain an org claims, enforcing per-domain uniqueness.

        Only ``EMAIL_DOMAIN`` orgs claim a domain (everyone else stores NULL, so
        same-domain users can still create their own orgs). Attempting to claim a
        domain already held by another ``EMAIL_DOMAIN`` org raises a conflict.
        """
        if join_policy != OrganizationJoinPolicy.EMAIL_DOMAIN:
            return None

        owner_domain = self._normalizable_email_domain(str(owner.email))
        if owner_domain is None:
            raise IdentityValidationError(
                "The EMAIL_DOMAIN join policy requires a work email domain"
            )
        if provided_domain:
            normalized = provided_domain.strip().lower()
            if normalized != owner_domain:
                raise IdentityValidationError(
                    "Organization email domain must match the owner's email domain"
                )

        existing_domain = await self.organization_repository.get_email_domain_org(
            owner_domain
        )
        if existing_domain and existing_domain.id != exclude_org_id:
            raise OrganizationConflictError(
                "This email domain is already taken by another organization"
            )
        return owner_domain

    async def create_organization(
        self, entity: OrganizationEntity, owner_user_id: UUID
    ) -> OrganizationEntity:
        owner = await self.user_repository.get(owner_user_id)
        if not owner:
            raise UserNotFoundError()

        existing_name = await self.organization_repository.get_by_name(entity.name)
        if existing_name:
            raise OrganizationConflictError(
                "Organization with this name already exists"
            )

        if not entity.slug:
            entity.slug = slugify(entity.name)

        existing_slug = await self.organization_repository.get_by_slug(entity.slug)
        if existing_slug:
            raise OrganizationConflictError("Organization slug already exists")

        entity.email_domain = await self._resolve_email_domain_for_policy(
            owner=owner,
            join_policy=entity.join_policy,
            provided_domain=entity.email_domain,
        )

        organization = await self.organization_repository.create(entity)

        owner_member = OrganizationMemberEntity(
            user_id=owner_user_id,
            organization_id=organization.id,
            role=OrganizationRole.ORG_OWNER,
        )
        await self.organization_repository.add_member(owner_member)

        return organization

    async def update_organization(
        self,
        org_id: UUID,
        requester_user_id: UUID,
        *,
        name: str | None = None,
        join_policy: OrganizationJoinPolicy | None = None,
        email_domain: str | None = None,
    ) -> OrganizationEntity:
        organization = await self.organization_repository.get(org_id)
        if not organization:
            raise OrganizationNotFoundError()

        await self._require_member(
            user_id=requester_user_id,
            organization_id=org_id,
            allowed_roles=[OrganizationRole.ORG_OWNER],
            denied_message="Only owners can update the organization",
        )

        requester = await self.user_repository.get(requester_user_id)
        if not requester:
            raise UserNotFoundError()

        if name is not None and name != organization.name:
            existing_name = await self.organization_repository.get_by_name(name)
            if existing_name and existing_name.id != org_id:
                raise OrganizationConflictError(
                    "Organization with this name already exists"
                )
            organization.name = name  # slug is a stable handle; not renamed

        new_policy = join_policy if join_policy is not None else organization.join_policy
        provided_domain = (
            email_domain if email_domain is not None else organization.email_domain
        )
        organization.email_domain = await self._resolve_email_domain_for_policy(
            owner=requester,
            join_policy=new_policy,
            provided_domain=provided_domain,
            exclude_org_id=org_id,
        )
        organization.join_policy = new_policy

        return await self.organization_repository.update(organization)

    async def is_slug_available(self, slug: str) -> bool:
        normalized_slug = slugify(slug)
        if not normalized_slug:
            raise IdentityValidationError("Slug is required")
        return await self.organization_repository.get_by_slug(normalized_slug) is None

    async def get_organization(
        self,
        org_id: UUID,
        requester_user_id: UUID,
    ) -> OrganizationEntity:
        await self._require_member(
            user_id=requester_user_id,
            organization_id=org_id,
            denied_message="You do not have access to this organization",
        )

        organization = await self.organization_repository.get(org_id)
        if not organization:
            raise OrganizationNotFoundError()

        return organization

    async def list_user_organizations(
        self,
        user_id: UUID,
        limit: int = 100,
        page_token: Optional[str] = None,
    ) -> Tuple[Sequence[OrganizationEntity], Optional[str]]:
        return await self.organization_repository.get_user_organizations(
            user_id, limit, page_token
        )

    async def list_suggested_organizations(
        self,
        user_id: UUID,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[OrganizationEntity], Optional[str]]:
        user = await self.user_repository.get(user_id)
        if not user:
            raise UserNotFoundError()

        domain = self._normalizable_email_domain(str(user.email))
        if domain is None:
            return [], None

        return await self.organization_repository.list_auto_join_organizations_by_email_domain(
            domain,
            user_id,
            limit,
            cursor,
        )

    async def join_auto_join_organization(
        self,
        organization_id: UUID,
        user_id: UUID,
    ) -> OrganizationEntity:
        user = await self.user_repository.get(user_id)
        if not user:
            raise UserNotFoundError()

        organization = await self.organization_repository.get(organization_id)
        if not organization:
            raise OrganizationNotFoundError()

        existing_member = await self.organization_repository.get_member(
            user_id, organization_id
        )
        if existing_member:
            return organization

        if not self._can_self_join(organization, user):
            raise IdentityAccessDeniedError(
                "This organization does not allow you to join"
            )

        member = OrganizationMemberEntity(
            user_id=user_id,
            organization_id=organization.id,
            role=OrganizationRole.ORG_MEMBER,
        )
        await self.organization_repository.add_member(member)

        return organization

    def _can_self_join(
        self, organization: OrganizationEntity, user: UserEntity
    ) -> bool:
        if organization.join_policy == OrganizationJoinPolicy.PUBLIC:
            return True
        if organization.join_policy == OrganizationJoinPolicy.EMAIL_DOMAIN:
            user_domain = self._normalizable_email_domain(str(user.email))
            return bool(organization.email_domain) and (
                user_domain == organization.email_domain
            )
        return False

    async def get_member(
        self, user_id: UUID, organization_id: UUID
    ) -> Optional[OrganizationMemberEntity]:
        return await self.organization_repository.get_member(user_id, organization_id)

    async def list_organization_members(
        self,
        organization_id: UUID,
        requester_user_id: UUID,
        limit: int = 100,
        cursor: Optional[str] = None,
    ):
        await self._require_member(
            user_id=requester_user_id,
            organization_id=organization_id,
            denied_message="You do not have access to this organization",
        )
        return await self.organization_repository.list_organization_members(
            organization_id, limit, cursor
        )

    async def create_invitation(
        self,
        entity: OrganizationInvitationEntity,
        inviter_user_id: UUID,
    ) -> OrganizationInvitationEntity:
        organization = await self.organization_repository.get(entity.organization_id)
        if not organization:
            raise OrganizationNotFoundError()

        inviter = await self._require_member(
            user_id=inviter_user_id,
            organization_id=entity.organization_id,
            allowed_roles=[OrganizationRole.ORG_OWNER, OrganizationRole.ORG_EDITOR],
            denied_message="Only owners and editors can invite members",
        )

        existing_member = await self.organization_repository.get_member_by_email(
            entity.organization_id,
            entity.email,
        )
        if existing_member:
            raise OrganizationConflictError(
                "User is already a member of this organization"
            )

        existing_invitation = (
            await self.organization_repository.get_invitation_by_email(
                entity.organization_id,
                entity.email,
            )
        )
        if existing_invitation:
            raise OrganizationConflictError(
                "An invitation already exists for this email"
            )

        pod_name: str | None = None
        pod_description: str | None = None
        if entity.pod_id is not None and self.pod_membership_port is not None:
            pod_details = await self.pod_membership_port.get_pod_invitation_details(
                entity.pod_id
            )
            pod_org_id = pod_details[2] if pod_details else None
            if pod_org_id is None:
                raise IdentityValidationError("Pod not found")
            if pod_org_id != entity.organization_id:
                raise IdentityValidationError(
                    "Pod does not belong to this organization"
                )
            pod_name, pod_description, _ = pod_details
            entity.pod_name = pod_name
            entity.pod_description = pod_description

        inviter_email = (
            inviter.user.email if inviter.user else "organization-member@lemma.local"
        )
        entity.mark_created(
            organization_name=organization.name,
            invited_by_user_id=inviter_user_id,
            invited_by_email=inviter_email,
            accept_url=self._build_invitation_accept_url(entity.id),
            pod_name=pod_name,
            pod_description=pod_description,
        )
        persisted = await self.organization_repository.add_invitation(entity)
        persisted.organization_name = organization.name
        persisted.pod_name = pod_name
        persisted.pod_description = pod_description
        return persisted

    async def list_invitations(
        self,
        organization_id: UUID,
        requester_user_id: UUID,
        status: OrganizationInvitationStatus
        | None = OrganizationInvitationStatus.PENDING,
        limit: int = 100,
        cursor: Optional[str] = None,
    ):
        await self._require_member(
            user_id=requester_user_id,
            organization_id=organization_id,
            allowed_roles=[OrganizationRole.ORG_OWNER, OrganizationRole.ORG_EDITOR],
            denied_message="Only owners and editors can view invitations",
        )

        invitations, next_cursor = (
            await self.organization_repository.list_organization_invitations(
                organization_id,
                status,
                limit,
                cursor,
            )
        )
        return (
            await self._enrich_invitation_list_display_fields(invitations),
            next_cursor,
        )

    async def list_user_invitations(
        self,
        requester_user_id: UUID,
        status: OrganizationInvitationStatus
        | None = OrganizationInvitationStatus.PENDING,
        limit: int = 100,
        cursor: Optional[str] = None,
    ):
        user = await self.user_repository.get(requester_user_id)
        if not user:
            raise UserNotFoundError()

        invitations, next_cursor = await self.organization_repository.list_user_invitations(
            user_email=str(user.email),
            status=status,
            limit=limit,
            cursor=cursor,
        )
        return (
            await self._enrich_invitation_list_display_fields(invitations),
            next_cursor,
        )

    async def get_invitation(
        self,
        invitation_id: UUID,
        requester_user_id: UUID,
        organization_id: UUID | None = None,
    ) -> OrganizationInvitationEntity:
        invitation = await self.organization_repository.get_invitation_by_id(
            invitation_id
        )
        if not invitation:
            raise OrganizationInvitationNotFoundError()
        invitation = await self._mark_invitation_expired_if_needed(invitation)

        if organization_id and invitation.organization_id != organization_id:
            raise IdentityValidationError("Invitation does not belong to organization")

        user = await self.user_repository.get(requester_user_id)
        if not user:
            raise UserNotFoundError()

        is_invitee = str(user.email).lower() == invitation.email.lower()
        if not is_invitee:
            await self._require_member(
                user_id=requester_user_id,
                organization_id=invitation.organization_id,
                allowed_roles=[
                    OrganizationRole.ORG_OWNER,
                    OrganizationRole.ORG_EDITOR,
                ],
                denied_message="Only invitee, owners, or editors can view invitations",
            )

        return await self._enrich_invitation_display_fields(invitation)

    async def accept_invitation(self, invitation_id: UUID, user_id: UUID):
        invitation = await self.organization_repository.get_invitation_by_id(
            invitation_id
        )
        if not invitation:
            raise OrganizationInvitationNotFoundError()
        invitation = await self._mark_invitation_expired_if_needed(invitation)

        if invitation.status != OrganizationInvitationStatus.PENDING:
            raise IdentityValidationError(
                f"Invitation is not pending (status: {invitation.status.value})"
            )

        user = await self.user_repository.get(user_id)
        if not user:
            raise UserNotFoundError()

        if str(user.email).lower() != invitation.email.lower():
            raise IdentityAccessDeniedError("This invitation is not for your email")

        organization = await self.organization_repository.get(
            invitation.organization_id
        )
        if not organization:
            raise OrganizationNotFoundError()

        existing_member = await self.organization_repository.get_member(
            user_id,
            invitation.organization_id,
        )
        if existing_member:
            raise OrganizationConflictError("User is already a member")

        member = OrganizationMemberEntity(
            user_id=user_id,
            organization_id=invitation.organization_id,
            role=invitation.role,
        )

        invitation.mark_accepted(
            accepted_user_id=user_id,
            accepted_email=str(user.email),
            organization_name=organization.name,
        )

        persisted_member = await self.organization_repository.add_member(member)
        await self.organization_repository.update_invitation(invitation)

        if invitation.pod_id is not None and self.pod_membership_port is not None:
            pod_org_id = await self.pod_membership_port.get_pod_organization_id(
                invitation.pod_id
            )
            if pod_org_id is not None:
                user_name_parts = [
                    part for part in [user.first_name, user.last_name] if part
                ]
                user_name = " ".join(user_name_parts) or None
                pod_role = invitation.pod_role or "POD_USER"
                await self.pod_membership_port.add_member_to_pod(
                    pod_id=invitation.pod_id,
                    organization_member_id=persisted_member.id,
                    user_id=user_id,
                    user_email=str(user.email),
                    user_name=user_name,
                    pod_role=pod_role,
                )

        return persisted_member

    async def revoke_invitation(
        self,
        invitation_id: UUID,
        requester_user_id: UUID,
        organization_id: UUID | None = None,
    ):
        invitation = await self.organization_repository.get_invitation_by_id(
            invitation_id
        )
        if not invitation:
            raise OrganizationInvitationNotFoundError()
        invitation = await self._mark_invitation_expired_if_needed(invitation)

        if organization_id and invitation.organization_id != organization_id:
            raise IdentityValidationError("Invitation does not belong to organization")

        await self._require_member(
            user_id=requester_user_id,
            organization_id=invitation.organization_id,
            allowed_roles=[OrganizationRole.ORG_OWNER, OrganizationRole.ORG_EDITOR],
            denied_message="Only owners and editors can revoke invitations",
        )

        if invitation.status != OrganizationInvitationStatus.PENDING:
            raise IdentityValidationError(
                f"Invitation is not pending (status: {invitation.status.value})"
            )

        invitation.mark_revoked(datetime.now(timezone.utc))
        await self.organization_repository.update_invitation(invitation)

    async def update_member_role(
        self,
        member_id: UUID,
        new_role: OrganizationRole,
        requester_user_id: UUID,
        organization_id: UUID | None = None,
    ) -> OrganizationMemberEntity:
        member = await self.organization_repository.get_member_by_id(member_id)
        if not member:
            raise OrganizationMemberNotFoundError()

        if organization_id and member.organization_id != organization_id:
            raise IdentityValidationError("Member does not belong to organization")

        await self._require_member(
            user_id=requester_user_id,
            organization_id=member.organization_id,
            allowed_roles=[OrganizationRole.ORG_OWNER],
            denied_message="Only owners can change roles",
        )

        member.update_role(new_role)
        return await self.organization_repository.update_member(member)

    async def remove_member(
        self,
        member_id: UUID,
        requester_user_id: UUID,
        organization_id: UUID | None = None,
    ) -> None:
        member = await self.organization_repository.get_member_by_id(member_id)
        if not member:
            raise OrganizationMemberNotFoundError()

        if organization_id and member.organization_id != organization_id:
            raise IdentityValidationError("Member does not belong to organization")

        is_self = member.user_id == requester_user_id
        if not is_self:
            requester_member = await self._require_member(
                user_id=requester_user_id,
                organization_id=member.organization_id,
                allowed_roles=[
                    OrganizationRole.ORG_OWNER,
                    OrganizationRole.ORG_EDITOR,
                ],
                denied_message="Only owners and editors can remove members",
            )
            if (
                requester_member.role == OrganizationRole.ORG_EDITOR
                and member.role == OrganizationRole.ORG_OWNER
            ):
                raise IdentityAccessDeniedError(
                    "Editors cannot remove organization owners"
                )

        deleted = await self.organization_repository.delete_member(member_id)
        if not deleted:
            raise OrganizationMemberNotFoundError()
