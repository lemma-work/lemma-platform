from __future__ import annotations

from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

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
    OrganizationMemberEntity,
    OrganizationRole,
)
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.services.organization_service import OrganizationService


def _member(
    *,
    user_id,
    organization_id,
    role: OrganizationRole,
    with_user: bool = True,
) -> OrganizationMemberEntity:
    return OrganizationMemberEntity(
        user_id=user_id,
        organization_id=organization_id,
        role=role,
        user=UserEntity(email="test+owner@example.com") if with_user else None,
    )


@pytest.mark.asyncio
async def test_create_organization_raises_conflict_by_name(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    organization_repository_mock.get_by_name.return_value = OrganizationEntity(
        name="Acme", slug="acme"
    )

    with pytest.raises(OrganizationConflictError):
        await organization_service.create_organization(
            OrganizationEntity(name="Acme", slug="acme"),
            owner_user_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_create_organization_raises_conflict_by_slug(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    organization_repository_mock.get_by_name.return_value = None
    organization_repository_mock.get_by_slug.return_value = OrganizationEntity(
        name="Other", slug="acme"
    )

    with pytest.raises(OrganizationConflictError):
        await organization_service.create_organization(
            OrganizationEntity(name="Acme", slug="acme"),
            owner_user_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_create_organization_success_adds_owner_member(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    owner_id = uuid4()
    org = OrganizationEntity(name="Acme", slug="acme")

    organization_repository_mock.get_by_name.return_value = None
    organization_repository_mock.get_by_slug.return_value = None
    organization_repository_mock.create.return_value = org

    created = await organization_service.create_organization(org, owner_id)

    assert created == org
    member_arg = organization_repository_mock.add_member.await_args.args[0]
    assert member_arg.organization_id == org.id
    assert member_arg.user_id == owner_id
    assert member_arg.role == OrganizationRole.ORG_OWNER


@pytest.mark.asyncio
async def test_get_organization_requires_membership(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    org_id = uuid4()
    organization_repository_mock.get_member.return_value = None

    with pytest.raises(IdentityAccessDeniedError):
        await organization_service.get_organization(org_id, uuid4())


@pytest.mark.asyncio
async def test_get_organization_raises_not_found(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    org_id = uuid4()
    requester_id = uuid4()
    organization_repository_mock.get_member.return_value = _member(
        user_id=requester_id,
        organization_id=org_id,
        role=OrganizationRole.ORG_MEMBER,
    )
    organization_repository_mock.get.return_value = None

    with pytest.raises(OrganizationNotFoundError):
        await organization_service.get_organization(org_id, requester_id)


@pytest.mark.asyncio
async def test_list_organization_members_requires_membership(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    organization_repository_mock.get_member.return_value = None

    with pytest.raises(IdentityAccessDeniedError):
        await organization_service.list_organization_members(
            organization_id=uuid4(),
            requester_user_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_create_invitation_requires_editor_or_owner(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    org = OrganizationEntity(name="Acme", slug="acme")
    invitation = OrganizationInvitationEntity(
        email="test+new@example.com",
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
    )

    organization_repository_mock.get.return_value = org
    organization_repository_mock.get_member.return_value = _member(
        user_id=uuid4(),
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
    )

    with pytest.raises(IdentityAccessDeniedError):
        await organization_service.create_invitation(invitation, inviter_user_id=uuid4())


@pytest.mark.asyncio
async def test_create_invitation_raises_member_conflict(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    inviter_id = uuid4()
    org = OrganizationEntity(name="Acme", slug="acme")
    invitation = OrganizationInvitationEntity(
        email="test+new@example.com",
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
    )

    organization_repository_mock.get.return_value = org
    organization_repository_mock.get_member.return_value = _member(
        user_id=inviter_id,
        organization_id=org.id,
        role=OrganizationRole.ORG_OWNER,
    )
    organization_repository_mock.get_member_by_email.return_value = _member(
        user_id=uuid4(),
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
    )

    with pytest.raises(OrganizationConflictError):
        await organization_service.create_invitation(invitation, inviter_user_id=inviter_id)


@pytest.mark.asyncio
async def test_create_invitation_raises_existing_invitation_conflict(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    inviter_id = uuid4()
    org = OrganizationEntity(name="Acme", slug="acme")
    invitation = OrganizationInvitationEntity(
        email="test+new@example.com",
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
    )

    organization_repository_mock.get.return_value = org
    organization_repository_mock.get_member.return_value = _member(
        user_id=inviter_id,
        organization_id=org.id,
        role=OrganizationRole.ORG_OWNER,
    )
    organization_repository_mock.get_member_by_email.return_value = None
    organization_repository_mock.get_invitation_by_email.return_value = invitation

    with pytest.raises(OrganizationConflictError):
        await organization_service.create_invitation(invitation, inviter_user_id=inviter_id)


@pytest.mark.asyncio
async def test_create_invitation_emits_event_with_accept_url(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    inviter_id = uuid4()
    org = OrganizationEntity(name="Acme", slug="acme")
    invitation = OrganizationInvitationEntity(
        email="test+new@example.com",
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
    )

    organization_repository_mock.get.return_value = org
    organization_repository_mock.get_member.return_value = _member(
        user_id=inviter_id,
        organization_id=org.id,
        role=OrganizationRole.ORG_OWNER,
    )
    organization_repository_mock.get_member_by_email.return_value = None
    organization_repository_mock.get_invitation_by_email.return_value = None
    organization_repository_mock.add_invitation.return_value = invitation

    await organization_service.create_invitation(invitation, inviter_user_id=inviter_id)

    create_arg = organization_repository_mock.add_invitation.await_args.args[0]
    events = create_arg.collect_events()
    assert len(events) == 1
    assert events[0].event_type == "identity.organization.invitation.created"
    assert events[0].accept_url.endswith(f"/invitations/{invitation.id}/accept")


@pytest.mark.asyncio
async def test_list_invitations_requires_editor_or_owner(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    org_id = uuid4()
    requester_id = uuid4()
    organization_repository_mock.get_member.return_value = _member(
        user_id=requester_id,
        organization_id=org_id,
        role=OrganizationRole.ORG_MEMBER,
    )

    with pytest.raises(IdentityAccessDeniedError):
        await organization_service.list_invitations(
            organization_id=org_id,
            requester_user_id=requester_id,
        )


@pytest.mark.asyncio
async def test_list_user_invitations_uses_user_email(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
    user_repository_mock: AsyncMock,
):
    user_id = uuid4()
    user_repository_mock.get.return_value = UserEntity(email="test+invitee@example.com")
    organization_repository_mock.list_user_invitations.return_value = ([], None)

    await organization_service.list_user_invitations(
        requester_user_id=user_id,
        limit=25,
        cursor="cursor-token",
    )

    user_repository_mock.get.assert_awaited_once_with(user_id)
    organization_repository_mock.list_user_invitations.assert_awaited_once_with(
        user_email="test+invitee@example.com",
        status=OrganizationInvitationStatus.PENDING,
        limit=25,
        cursor="cursor-token",
    )


@pytest.mark.asyncio
async def test_get_invitation_denies_non_invitee_non_manager(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
    user_repository_mock: AsyncMock,
):
    org_id = uuid4()
    requester_id = uuid4()
    invitation = OrganizationInvitationEntity(
        email="test+invitee@example.com",
        organization_id=org_id,
        role=OrganizationRole.ORG_MEMBER,
    )
    organization_repository_mock.get_invitation_by_id.return_value = invitation
    organization_repository_mock.get_member.return_value = _member(
        user_id=requester_id,
        organization_id=org_id,
        role=OrganizationRole.ORG_MEMBER,
    )
    user_repository_mock.get.return_value = UserEntity(email="test+other@example.com")

    with pytest.raises(IdentityAccessDeniedError):
        await organization_service.get_invitation(
            invitation_id=invitation.id,
            requester_user_id=requester_id,
            organization_id=org_id,
        )


@pytest.mark.asyncio
async def test_get_invitation_allows_invitee_user(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
    user_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    invitation = OrganizationInvitationEntity(
        email="test+invitee@example.com",
        organization_id=uuid4(),
        role=OrganizationRole.ORG_MEMBER,
    )
    organization_repository_mock.get_invitation_by_id.return_value = invitation
    user_repository_mock.get.return_value = UserEntity(email="test+invitee@example.com")

    found = await organization_service.get_invitation(
        invitation_id=invitation.id,
        requester_user_id=requester_id,
    )

    assert found.id == invitation.id
    organization_repository_mock.get_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_invitation_validates_org_in_path(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
    user_repository_mock: AsyncMock,
):
    invitation = OrganizationInvitationEntity(
        email="test+invitee@example.com",
        organization_id=uuid4(),
        role=OrganizationRole.ORG_MEMBER,
    )
    organization_repository_mock.get_invitation_by_id.return_value = invitation
    user_repository_mock.get.return_value = UserEntity(email="test+invitee@example.com")

    with pytest.raises(IdentityValidationError):
        await organization_service.get_invitation(
            invitation_id=invitation.id,
            requester_user_id=uuid4(),
            organization_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_accept_invitation_not_found(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    organization_repository_mock.get_invitation_by_id.return_value = None

    with pytest.raises(OrganizationInvitationNotFoundError):
        await organization_service.accept_invitation(uuid4(), uuid4())


@pytest.mark.asyncio
async def test_accept_invitation_user_not_found(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
    user_repository_mock: AsyncMock,
):
    invitation = OrganizationInvitationEntity(
        email="test+invitee@example.com",
        organization_id=uuid4(),
        role=OrganizationRole.ORG_MEMBER,
    )
    organization_repository_mock.get_invitation_by_id.return_value = invitation
    user_repository_mock.get.return_value = None

    with pytest.raises(UserNotFoundError):
        await organization_service.accept_invitation(invitation.id, uuid4())


@pytest.mark.asyncio
async def test_accept_invitation_email_mismatch(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
    user_repository_mock: AsyncMock,
):
    invitation = OrganizationInvitationEntity(
        email="test+invitee@example.com",
        organization_id=uuid4(),
        role=OrganizationRole.ORG_MEMBER,
    )
    user = UserEntity(email="test+other@example.com")

    organization_repository_mock.get_invitation_by_id.return_value = invitation
    user_repository_mock.get.return_value = user

    with pytest.raises(IdentityAccessDeniedError):
        await organization_service.accept_invitation(invitation.id, user.id)


@pytest.mark.asyncio
async def test_accept_invitation_raises_conflict_when_member_exists(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
    user_repository_mock: AsyncMock,
):
    org = OrganizationEntity(name="Acme", slug="acme")
    user = UserEntity(email="test+invitee@example.com")
    invitation = OrganizationInvitationEntity(
        email="test+invitee@example.com",
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
    )

    organization_repository_mock.get_invitation_by_id.return_value = invitation
    user_repository_mock.get.return_value = user
    organization_repository_mock.get.return_value = org
    organization_repository_mock.get_member.return_value = _member(
        user_id=user.id,
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
    )

    with pytest.raises(OrganizationConflictError):
        await organization_service.accept_invitation(invitation.id, user.id)


@pytest.mark.asyncio
async def test_accept_invitation_adds_member_and_emits_event(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
    user_repository_mock: AsyncMock,
):
    org = OrganizationEntity(name="Acme", slug="acme")
    user = UserEntity(email="test+invitee@example.com")
    invitation = OrganizationInvitationEntity(
        email="test+invitee@example.com",
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
    )

    persisted_member = OrganizationMemberEntity(
        user_id=user.id,
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
    )

    organization_repository_mock.get_invitation_by_id.return_value = invitation
    user_repository_mock.get.return_value = user
    organization_repository_mock.get.return_value = org
    organization_repository_mock.get_member.return_value = None
    organization_repository_mock.add_member.return_value = persisted_member

    member = await organization_service.accept_invitation(invitation.id, user.id)

    assert member.role == OrganizationRole.ORG_MEMBER
    organization_repository_mock.add_member.assert_awaited_once()
    update_arg = organization_repository_mock.update_invitation.await_args.args[0]
    events = update_arg.collect_events()
    assert len(events) == 1
    assert events[0].event_type == "identity.organization.invitation.accepted"
    assert update_arg.status == OrganizationInvitationStatus.ACCEPTED


@pytest.mark.asyncio
async def test_revoke_invitation_validates_org_in_path(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    invitation = OrganizationInvitationEntity(
        email="test+invitee@example.com",
        organization_id=uuid4(),
        role=OrganizationRole.ORG_MEMBER,
    )
    organization_repository_mock.get_invitation_by_id.return_value = invitation

    with pytest.raises(IdentityValidationError):
        await organization_service.revoke_invitation(
            invitation_id=invitation.id,
            requester_user_id=uuid4(),
            organization_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_revoke_invitation_updates_status(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    org_id = uuid4()
    invitation = OrganizationInvitationEntity(
        email="test+invitee@example.com",
        organization_id=org_id,
        role=OrganizationRole.ORG_MEMBER,
    )
    organization_repository_mock.get_invitation_by_id.return_value = invitation
    organization_repository_mock.get_member.return_value = _member(
        user_id=requester_id,
        organization_id=org_id,
        role=OrganizationRole.ORG_OWNER,
    )

    await organization_service.revoke_invitation(
        invitation_id=invitation.id,
        requester_user_id=requester_id,
    )

    update_arg = organization_repository_mock.update_invitation.await_args.args[0]
    assert update_arg.status == OrganizationInvitationStatus.REVOKED


@pytest.mark.asyncio
async def test_update_member_role_requires_owner(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    member = OrganizationMemberEntity(
        user_id=uuid4(),
        organization_id=uuid4(),
        role=OrganizationRole.ORG_MEMBER,
    )
    organization_repository_mock.get_member_by_id.return_value = member
    organization_repository_mock.get_member.return_value = _member(
        user_id=uuid4(),
        organization_id=member.organization_id,
        role=OrganizationRole.ORG_EDITOR,
    )

    with pytest.raises(IdentityAccessDeniedError):
        await organization_service.update_member_role(
            member.id,
            OrganizationRole.ORG_EDITOR,
            requester_user_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_update_member_role_not_found(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    organization_repository_mock.get_member_by_id.return_value = None

    with pytest.raises(OrganizationMemberNotFoundError):
        await organization_service.update_member_role(
            uuid4(),
            OrganizationRole.ORG_EDITOR,
            requester_user_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_remove_member_allows_self_remove_without_owner(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    user_id = uuid4()
    member = OrganizationMemberEntity(
        user_id=user_id,
        organization_id=uuid4(),
        role=OrganizationRole.ORG_MEMBER,
    )

    organization_repository_mock.get_member_by_id.return_value = member
    organization_repository_mock.delete_member.return_value = True

    await organization_service.remove_member(
        member_id=member.id,
        requester_user_id=user_id,
    )

    organization_repository_mock.get_member.assert_not_called()


@pytest.mark.asyncio
async def test_remove_member_allows_editor_for_other_non_owner_user(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    member = OrganizationMemberEntity(
        user_id=uuid4(),
        organization_id=uuid4(),
        role=OrganizationRole.ORG_MEMBER,
    )
    requester = uuid4()

    organization_repository_mock.get_member_by_id.return_value = member
    organization_repository_mock.get_member.return_value = _member(
        user_id=requester,
        organization_id=member.organization_id,
        role=OrganizationRole.ORG_EDITOR,
    )
    organization_repository_mock.delete_member.return_value = True

    await organization_service.remove_member(
        member_id=member.id,
        requester_user_id=requester,
    )


@pytest.mark.asyncio
async def test_remove_member_blocks_editor_from_removing_owner(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
):
    owner_member = OrganizationMemberEntity(
        user_id=uuid4(),
        organization_id=uuid4(),
        role=OrganizationRole.ORG_OWNER,
    )
    requester = uuid4()

    organization_repository_mock.get_member_by_id.return_value = owner_member
    organization_repository_mock.get_member.return_value = _member(
        user_id=requester,
        organization_id=owner_member.organization_id,
        role=OrganizationRole.ORG_EDITOR,
    )

    with pytest.raises(IdentityAccessDeniedError):
        await organization_service.remove_member(
            member_id=owner_member.id,
            requester_user_id=requester,
        )


@pytest.mark.asyncio
async def test_create_invitation_with_pod_id_validates_pod_belongs_to_org(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
    pod_membership_port_mock: AsyncMock,
):
    inviter_id = uuid4()
    org = OrganizationEntity(name="Acme", slug="acme")
    pod_id = uuid4()
    invitation = OrganizationInvitationEntity(
        email="test+new@example.com",
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
        pod_id=pod_id,
        pod_role="POD_USER",
    )

    organization_repository_mock.get.return_value = org
    organization_repository_mock.get_member.return_value = _member(
        user_id=inviter_id,
        organization_id=org.id,
        role=OrganizationRole.ORG_OWNER,
    )
    organization_repository_mock.get_member_by_email.return_value = None
    organization_repository_mock.get_invitation_by_email.return_value = None
    organization_repository_mock.add_invitation.return_value = invitation

    other_org_id = uuid4()
    pod_membership_port_mock.get_pod_invitation_details.return_value = (
        "Other Pod",
        None,
        other_org_id,
    )

    with pytest.raises(IdentityValidationError, match="Pod does not belong"):
        await organization_service.create_invitation(invitation, inviter_user_id=inviter_id)


@pytest.mark.asyncio
async def test_create_invitation_with_pod_id_raises_when_pod_not_found(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
    pod_membership_port_mock: AsyncMock,
):
    inviter_id = uuid4()
    org = OrganizationEntity(name="Acme", slug="acme")
    pod_id = uuid4()
    invitation = OrganizationInvitationEntity(
        email="test+new@example.com",
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
        pod_id=pod_id,
    )

    organization_repository_mock.get.return_value = org
    organization_repository_mock.get_member.return_value = _member(
        user_id=inviter_id,
        organization_id=org.id,
        role=OrganizationRole.ORG_OWNER,
    )
    organization_repository_mock.get_member_by_email.return_value = None
    organization_repository_mock.get_invitation_by_email.return_value = None
    pod_membership_port_mock.get_pod_invitation_details.return_value = None

    with pytest.raises(IdentityValidationError, match="Pod not found"):
        await organization_service.create_invitation(invitation, inviter_user_id=inviter_id)


@pytest.mark.asyncio
async def test_create_invitation_with_pod_id_succeeds_when_pod_in_same_org(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
    pod_membership_port_mock: AsyncMock,
):
    inviter_id = uuid4()
    org = OrganizationEntity(name="Acme", slug="acme")
    pod_id = uuid4()
    invitation = OrganizationInvitationEntity(
        email="test+new@example.com",
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
        pod_id=pod_id,
        pod_role="POD_USER",
    )

    organization_repository_mock.get.return_value = org
    organization_repository_mock.get_member.return_value = _member(
        user_id=inviter_id,
        organization_id=org.id,
        role=OrganizationRole.ORG_OWNER,
    )
    organization_repository_mock.get_member_by_email.return_value = None
    organization_repository_mock.get_invitation_by_email.return_value = None
    organization_repository_mock.add_invitation.return_value = invitation
    pod_membership_port_mock.get_pod_invitation_details.return_value = (
        "Build Pod",
        "Build things",
        org.id,
    )

    result = await organization_service.create_invitation(invitation, inviter_user_id=inviter_id)

    assert result.pod_id == pod_id
    assert result.pod_role == "POD_USER"
    assert invitation.pod_name == "Build Pod"
    assert invitation.pod_description == "Build things"
    pod_membership_port_mock.get_pod_invitation_details.assert_awaited_once_with(pod_id)


@pytest.mark.asyncio
async def test_accept_invitation_adds_to_pod_when_pod_id_set(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
    user_repository_mock: AsyncMock,
    pod_membership_port_mock: AsyncMock,
):
    org = OrganizationEntity(name="Acme", slug="acme")
    user = UserEntity(email="test+invitee@example.com")
    pod_id = uuid4()
    invitation = OrganizationInvitationEntity(
        email="test+invitee@example.com",
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
        pod_id=pod_id,
        pod_role="POD_EDITOR",
    )

    persisted_member = OrganizationMemberEntity(
        user_id=user.id,
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
    )

    organization_repository_mock.get_invitation_by_id.return_value = invitation
    user_repository_mock.get.return_value = user
    organization_repository_mock.get.return_value = org
    organization_repository_mock.get_member.return_value = None
    organization_repository_mock.add_member.return_value = persisted_member
    pod_membership_port_mock.get_pod_organization_id.return_value = org.id

    await organization_service.accept_invitation(invitation.id, user.id)

    pod_membership_port_mock.add_member_to_pod.assert_awaited_once()
    call_kwargs = pod_membership_port_mock.add_member_to_pod.await_args.kwargs
    assert call_kwargs["pod_id"] == pod_id
    assert call_kwargs["organization_member_id"] == persisted_member.id
    assert call_kwargs["user_id"] == user.id
    assert call_kwargs["user_email"] == str(user.email)
    assert call_kwargs["pod_role"] == "POD_EDITOR"


@pytest.mark.asyncio
async def test_accept_invitation_defaults_pod_role_to_POD_USER(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
    user_repository_mock: AsyncMock,
    pod_membership_port_mock: AsyncMock,
):
    org = OrganizationEntity(name="Acme", slug="acme")
    user = UserEntity(email="test+invitee@example.com")
    pod_id = uuid4()
    invitation = OrganizationInvitationEntity(
        email="test+invitee@example.com",
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
        pod_id=pod_id,
        pod_role=None,
    )

    persisted_member = OrganizationMemberEntity(
        user_id=user.id,
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
    )

    organization_repository_mock.get_invitation_by_id.return_value = invitation
    user_repository_mock.get.return_value = user
    organization_repository_mock.get.return_value = org
    organization_repository_mock.get_member.return_value = None
    organization_repository_mock.add_member.return_value = persisted_member
    pod_membership_port_mock.get_pod_organization_id.return_value = org.id

    await organization_service.accept_invitation(invitation.id, user.id)

    call_kwargs = pod_membership_port_mock.add_member_to_pod.await_args.kwargs
    assert call_kwargs["pod_role"] == "POD_USER"


@pytest.mark.asyncio
async def test_accept_invitation_skips_pod_when_pod_deleted(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
    user_repository_mock: AsyncMock,
    pod_membership_port_mock: AsyncMock,
):
    org = OrganizationEntity(name="Acme", slug="acme")
    user = UserEntity(email="test+invitee@example.com")
    pod_id = uuid4()
    invitation = OrganizationInvitationEntity(
        email="test+invitee@example.com",
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
        pod_id=pod_id,
    )

    persisted_member = OrganizationMemberEntity(
        user_id=user.id,
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
    )

    organization_repository_mock.get_invitation_by_id.return_value = invitation
    user_repository_mock.get.return_value = user
    organization_repository_mock.get.return_value = org
    organization_repository_mock.get_member.return_value = None
    organization_repository_mock.add_member.return_value = persisted_member
    pod_membership_port_mock.get_pod_organization_id.return_value = None

    member = await organization_service.accept_invitation(invitation.id, user.id)

    pod_membership_port_mock.add_member_to_pod.assert_not_awaited()
    assert member.role == OrganizationRole.ORG_MEMBER


@pytest.mark.asyncio
async def test_accept_invitation_without_pod_id_does_not_call_pod_port(
    organization_service: OrganizationService,
    organization_repository_mock: AsyncMock,
    user_repository_mock: AsyncMock,
    pod_membership_port_mock: AsyncMock,
):
    org = OrganizationEntity(name="Acme", slug="acme")
    user = UserEntity(email="test+invitee@example.com")
    invitation = OrganizationInvitationEntity(
        email="test+invitee@example.com",
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
    )

    persisted_member = OrganizationMemberEntity(
        user_id=user.id,
        organization_id=org.id,
        role=OrganizationRole.ORG_MEMBER,
    )

    organization_repository_mock.get_invitation_by_id.return_value = invitation
    user_repository_mock.get.return_value = user
    organization_repository_mock.get.return_value = org
    organization_repository_mock.get_member.return_value = None
    organization_repository_mock.add_member.return_value = persisted_member

    await organization_service.accept_invitation(invitation.id, user.id)

    pod_membership_port_mock.get_pod_organization_id.assert_not_awaited()
    pod_membership_port_mock.add_member_to_pod.assert_not_awaited()
