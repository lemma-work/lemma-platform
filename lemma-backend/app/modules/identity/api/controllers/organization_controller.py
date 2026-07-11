from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from app.core.api.dependencies import UoWDep
from app.core.authorization.service import AuthorizationDataService
from app.core.api.pagination import parse_uuid_page_token
from app.core.helpers.slug import slugify
from app.modules.identity.api.dependencies import OrganizationServiceDep
from app.modules.identity.api.schemas.organization_schemas import (
    OrganizationMessageResponse,
    OrganizationCreateRequest,
    OrganizationInvitationListResponse,
    OrganizationInvitationRequest,
    OrganizationInvitationResponse,
    OrganizationListResponse,
    OrganizationMemberListResponse,
    OrganizationMemberResponse,
    OrganizationResponse,
    OrganizationSlugAvailabilityResponse,
    OrganizationUpdateRequest,
    UpdateMemberRoleRequest,
)
from app.modules.identity.domain.organization_entities import (
    OrganizationEntity,
    OrganizationInvitationEntity,
    OrganizationInvitationStatus,
    OrganizationRole,
)
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.infrastructure.models.organization_models import (
    OrganizationMember,
)

router = APIRouter(
    prefix="/organizations",
    tags=["Organizations"],
    redirect_slashes=False,
)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    operation_id="org.create",
    summary="Create Organization",
    description="Create a new organization",
    response_model=OrganizationResponse,
)
async def create_organization(
    request: Request,
    data: OrganizationCreateRequest,
    org_service: OrganizationServiceDep,
    uow: UoWDep,
) -> OrganizationResponse:
    """Create a new organization."""
    user: UserEntity = request.state.user
    entity = OrganizationEntity(
        name=data.name,
        slug=data.slug or "",
        email_domain=data.email_domain,
        join_policy=data.join_policy,
    )

    organization = await org_service.create_organization(
        entity=entity,
        owner_user_id=user.id,
    )
    await _sync_org_role_assignment(
        uow,
        organization_id=organization.id,
        user_id=user.id,
        role=OrganizationRole.ORG_OWNER,
        assigned_by_user_id=user.id,
    )
    return OrganizationResponse.model_validate(organization)


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    operation_id="org.list",
    summary="List My Organizations",
    description="Get all organizations the current user belongs to",
    response_model=OrganizationListResponse,
)
async def list_my_organizations(
    request: Request,
    org_service: OrganizationServiceDep,
    limit: int = 100,
    page_token: str | None = None,
) -> OrganizationListResponse:
    """Get all organizations for the current user."""
    parse_uuid_page_token(page_token)

    user: UserEntity = request.state.user
    organizations, next_cursor = await org_service.list_user_organizations(
        user.id, limit, page_token
    )

    return OrganizationListResponse(
        items=[OrganizationResponse.model_validate(org) for org in organizations],
        limit=limit,
        next_page_token=next_cursor,
    )


@router.get(
    "/suggested",
    status_code=status.HTTP_200_OK,
    operation_id="org.suggested",
    summary="Get Suggested Organizations",
    description="Get auto-join organizations matching the current user's email domain",
    response_model=OrganizationListResponse,
)
async def get_suggested_orgs(
    request: Request,
    org_service: OrganizationServiceDep,
    limit: int = 100,
    page_token: str | None = None,
) -> OrganizationListResponse:
    """Get suggested organizations for the current user."""
    parse_uuid_page_token(page_token)

    user: UserEntity = request.state.user
    organizations, next_cursor = await org_service.list_suggested_organizations(
        user.id, limit, page_token
    )

    return OrganizationListResponse(
        items=[OrganizationResponse.model_validate(org) for org in organizations],
        limit=limit,
        next_page_token=next_cursor,
    )


@router.get(
    "/slug-availability",
    status_code=status.HTTP_200_OK,
    operation_id="org.slug_availability",
    summary="Check Organization Slug Availability",
    description="Check whether an organization slug is available",
    response_model=OrganizationSlugAvailabilityResponse,
)
async def check_slug_availability(
    slug: str,
    org_service: OrganizationServiceDep,
) -> OrganizationSlugAvailabilityResponse:
    """Check organization slug availability."""
    normalized_slug = slugify(slug)
    available = await org_service.is_slug_available(normalized_slug)
    return OrganizationSlugAvailabilityResponse(
        slug=normalized_slug,
        available=available,
    )


@router.post(
    "/{org_id}/join",
    status_code=status.HTTP_200_OK,
    operation_id="org.join_auto_join",
    summary="Join Auto-Join Organization",
    description="Join an organization when the current user's email domain is allowed to auto-join",
    response_model=OrganizationResponse,
)
async def join_auto_join_organization(
    request: Request,
    org_id: UUID,
    org_service: OrganizationServiceDep,
    uow: UoWDep,
) -> OrganizationResponse:
    """Join an organization that allows automatic joining for the user's email domain."""
    user: UserEntity = request.state.user
    organization = await org_service.join_auto_join_organization(
        organization_id=org_id,
        user_id=user.id,
    )
    await _sync_org_role_assignment(
        uow,
        organization_id=org_id,
        user_id=user.id,
        role=OrganizationRole.ORG_MEMBER,
        assigned_by_user_id=user.id,
    )

    return OrganizationResponse.model_validate(organization)


@router.get(
    "/invitations",
    status_code=status.HTTP_200_OK,
    operation_id="org.invitation.list_mine",
    summary="List My Invitations",
    description="Get all pending invitations for the current user",
    response_model=OrganizationInvitationListResponse,
)
async def list_my_invitations(
    request: Request,
    org_service: OrganizationServiceDep,
    status: OrganizationInvitationStatus = OrganizationInvitationStatus.PENDING,
    limit: int = 100,
    page_token: str | None = None,
) -> OrganizationInvitationListResponse:
    """Get all invitations for the current user."""
    parse_uuid_page_token(page_token)

    user: UserEntity = request.state.user
    invitations, next_cursor = await org_service.list_user_invitations(
        requester_user_id=user.id,
        status=status,
        limit=limit,
        cursor=page_token,
    )

    return OrganizationInvitationListResponse(
        items=[
            OrganizationInvitationResponse.model_validate(inv) for inv in invitations
        ],
        limit=limit,
        next_page_token=next_cursor,
    )


@router.get(
    "/{org_id}",
    status_code=status.HTTP_200_OK,
    operation_id="org.get",
    summary="Get Organization",
    description="Get organization details",
    response_model=OrganizationResponse,
)
async def get_organization(
    request: Request,
    org_id: UUID,
    org_service: OrganizationServiceDep,
) -> OrganizationResponse:
    """Get organization details."""
    user: UserEntity = request.state.user
    organization = await org_service.get_organization(
        org_id=org_id,
        requester_user_id=user.id,
    )

    return OrganizationResponse.model_validate(organization)


@router.patch(
    "/{org_id}",
    status_code=status.HTTP_200_OK,
    operation_id="org.update",
    summary="Update Organization",
    description="Update an organization's name or join policy (owner only)",
    response_model=OrganizationResponse,
)
async def update_organization(
    request: Request,
    org_id: UUID,
    data: OrganizationUpdateRequest,
    org_service: OrganizationServiceDep,
) -> OrganizationResponse:
    """Update an organization (owner only)."""
    user: UserEntity = request.state.user
    organization = await org_service.update_organization(
        org_id=org_id,
        requester_user_id=user.id,
        name=data.name,
        join_policy=data.join_policy,
        email_domain=data.email_domain,
    )
    return OrganizationResponse.model_validate(organization)


@router.get(
    "/{org_id}/members",
    status_code=status.HTTP_200_OK,
    operation_id="org.member.list",
    summary="List Organization Members",
    description="Get all members of an organization",
    response_model=OrganizationMemberListResponse,
)
async def list_members(
    request: Request,
    org_id: UUID,
    org_service: OrganizationServiceDep,
    limit: int = 100,
    page_token: str | None = None,
) -> OrganizationMemberListResponse:
    """Get all members of an organization."""
    parse_uuid_page_token(page_token)

    user: UserEntity = request.state.user
    members, next_cursor = await org_service.list_organization_members(
        organization_id=org_id,
        requester_user_id=user.id,
        limit=limit,
        cursor=page_token,
    )

    return OrganizationMemberListResponse(
        items=[OrganizationMemberResponse.model_validate(m) for m in members],
        limit=limit,
        next_page_token=next_cursor,
    )


@router.post(
    "/{org_id}/invitations",
    status_code=status.HTTP_201_CREATED,
    operation_id="org.invitation.invite",
    summary="Invite Member",
    description="Invite a user to join the organization",
    response_model=OrganizationInvitationResponse,
)
async def invite_member(
    request: Request,
    org_id: UUID,
    data: OrganizationInvitationRequest,
    org_service: OrganizationServiceDep,
) -> OrganizationInvitationResponse:
    """Invite a user to join the organization."""
    user: UserEntity = request.state.user
    entity = OrganizationInvitationEntity(
        email=data.email,
        organization_id=org_id,
        role=data.role,
        pod_id=data.pod_id,
        pod_role=data.pod_role,
        redirect_uri=data.redirect_uri,
    )

    invitation = await org_service.create_invitation(
        entity=entity,
        inviter_user_id=user.id,
    )

    return OrganizationInvitationResponse.model_validate(invitation)


@router.get(
    "/{org_id}/invitations",
    status_code=status.HTTP_200_OK,
    operation_id="org.invitation.list",
    summary="List Organization Invitations",
    description="Get all pending invitations for an organization",
    response_model=OrganizationInvitationListResponse,
)
async def list_invitations(
    request: Request,
    org_id: UUID,
    org_service: OrganizationServiceDep,
    status: OrganizationInvitationStatus = OrganizationInvitationStatus.PENDING,
    limit: int = 100,
    page_token: str | None = None,
) -> OrganizationInvitationListResponse:
    """Get all invitations for an organization."""
    parse_uuid_page_token(page_token)

    user: UserEntity = request.state.user
    invitations, next_cursor = await org_service.list_invitations(
        organization_id=org_id,
        requester_user_id=user.id,
        status=status,
        limit=limit,
        cursor=page_token,
    )

    return OrganizationInvitationListResponse(
        items=[
            OrganizationInvitationResponse.model_validate(inv) for inv in invitations
        ],
        limit=limit,
        next_page_token=next_cursor,
    )


@router.post(
    "/invitations/{invitation_id}/accept",
    status_code=status.HTTP_200_OK,
    operation_id="org.invitation.accept",
    summary="Accept Invitation",
    description="Accept an organization invitation",
    response_model=OrganizationMessageResponse,
)
async def accept_invitation(
    request: Request,
    invitation_id: UUID,
    org_service: OrganizationServiceDep,
    uow: UoWDep,
) -> OrganizationMessageResponse:
    """Accept an organization invitation."""
    user: UserEntity = request.state.user
    await org_service.accept_invitation(
        invitation_id=invitation_id,
        user_id=user.id,
    )
    invitation = await org_service.get_invitation(
        invitation_id=invitation_id,
        requester_user_id=user.id,
    )
    await _sync_org_role_assignment(
        uow,
        organization_id=invitation.organization_id,
        user_id=user.id,
        role=OrganizationRole.ORG_MEMBER,
        assigned_by_user_id=user.id,
    )

    return OrganizationMessageResponse(
        message="Successfully joined the organization",
        success=True,
        redirect_uri=invitation.redirect_uri,
    )


@router.get(
    "/invitations/{invitation_id}",
    status_code=status.HTTP_200_OK,
    operation_id="org.invitation.get",
    summary="Get Organization Invitation",
    description="Get an invitation by id",
    response_model=OrganizationInvitationResponse,
)
async def get_invitation(
    request: Request,
    invitation_id: UUID,
    org_service: OrganizationServiceDep,
) -> OrganizationInvitationResponse:
    """Get an invitation by id."""
    user: UserEntity = request.state.user
    invitation = await org_service.get_invitation(
        invitation_id=invitation_id,
        requester_user_id=user.id,
    )
    return OrganizationInvitationResponse.model_validate(invitation)


@router.delete(
    "/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="org.invitation.revoke",
    summary="Revoke Invitation",
    description="Revoke an organization invitation",
)
async def revoke_invitation(
    request: Request,
    invitation_id: UUID,
    org_service: OrganizationServiceDep,
) -> None:
    """Revoke an organization invitation."""
    user: UserEntity = request.state.user
    await org_service.revoke_invitation(
        invitation_id=invitation_id,
        requester_user_id=user.id,
    )


@router.patch(
    "/{org_id}/members/{member_id}/role",
    status_code=status.HTTP_200_OK,
    operation_id="org.member.update_role",
    summary="Update Member Role",
    description="Update a member's role in the organization",
    response_model=OrganizationMemberResponse,
)
async def update_member_role(
    request: Request,
    org_id: UUID,
    member_id: UUID,
    data: UpdateMemberRoleRequest,
    org_service: OrganizationServiceDep,
    uow: UoWDep,
) -> OrganizationMemberResponse:
    """Update a member's role."""
    user: UserEntity = request.state.user
    member = await org_service.update_member_role(
        member_id=member_id,
        new_role=data.role,
        requester_user_id=user.id,
        organization_id=org_id,
    )
    await AuthorizationDataService(uow.session).assign_roles(
        organization_id=org_id,
        pod_id=None,
        principal_type="ORG_MEMBER",
        principal_id=member_id,
        role_names=[data.role.value],
        assigned_by_user_id=user.id,
    )
    return OrganizationMemberResponse.model_validate(member)


async def _sync_org_role_assignment(
    uow: UoWDep,
    *,
    organization_id: UUID,
    user_id: UUID,
    role: OrganizationRole,
    assigned_by_user_id: UUID | None,
) -> None:
    member_id = (
        await uow.session.execute(
            select(OrganizationMember.id).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if member_id is None:
        return
    await AuthorizationDataService(uow.session).assign_roles(
        organization_id=organization_id,
        pod_id=None,
        principal_type="ORG_MEMBER",
        principal_id=member_id,
        role_names=[role.value],
        assigned_by_user_id=assigned_by_user_id,
    )


@router.delete(
    "/{org_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="org.member.remove",
    summary="Remove Member",
    description="Remove a member from the organization",
)
async def remove_member(
    request: Request,
    org_id: UUID,
    member_id: UUID,
    org_service: OrganizationServiceDep,
    uow: UoWDep,
) -> None:
    """Remove a member from the organization."""
    user: UserEntity = request.state.user
    authz = AuthorizationDataService(uow.session)
    # Capture the member's authorization targets before removal: deleting the
    # org member cascades its pod memberships away, but their (FK-less) role
    # assignments and grants do not cascade. The read is harmless if the removal
    # is subsequently denied.
    targets = await authz.member_authorization_targets(
        organization_member_id=member_id
    )
    await org_service.remove_member(
        member_id=member_id,
        requester_user_id=user.id,
        organization_id=org_id,
    )
    if targets is not None:
        await authz.purge_member_authorization(targets)
