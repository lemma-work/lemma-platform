from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.identity.domain.organization_entities import (
    OrganizationMemberEntity,
    OrganizationRole,
)
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.pod.domain.errors import (
    PodAccessDeniedError,
    PodConflictError,
    PodMemberNotFoundError,
    PodNotFoundError,
)
from app.modules.pod.domain.pod_entities import PodEntity, PodMemberEntity, PodRole
from app.modules.pod.services.pod_member_service import PodMemberService


def _make_user(email: str = "test+u@example.com") -> UserEntity:
    return UserEntity(email=email)


def _make_org_member(
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
        user=_make_user() if with_user else None,
    )


def _make_pod(*, organization_id, user_id) -> PodEntity:
    return PodEntity(
        name="Test Pod",
        description="pod",
        organization_id=organization_id,
        user_id=user_id,
    )


@pytest.mark.asyncio
async def test_assign_member_success_adds_event(
    pod_member_service: PodMemberService,
    pod_repository_mock: AsyncMock,
    pod_member_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    organization_id = uuid4()
    target_user_id = uuid4()
    target_org_member_id = uuid4()

    pod = _make_pod(organization_id=organization_id, user_id=requester_id)
    member_to_add = PodMemberEntity(
        pod_id=pod.id,
        organization_member_id=target_org_member_id,
        role=PodRole.EDITOR,
    )

    pod_repository_mock.get.return_value = pod
    organization_repository_mock.get_member.return_value = _make_org_member(
        user_id=requester_id,
        organization_id=organization_id,
        role=OrganizationRole.ORG_OWNER,
    )
    requester_pod_member = PodMemberEntity(
        pod_id=pod.id,
        organization_member_id=uuid4(),
        role=PodRole.ADMIN,
    )
    pod_member_repository_mock.get_by_pod_and_org_member.side_effect = [
        requester_pod_member,
        None,
    ]
    organization_repository_mock.get_member_by_id.return_value = _make_org_member(
        user_id=target_user_id,
        organization_id=organization_id,
        role=OrganizationRole.ORG_MEMBER,
    )
    pod_member_repository_mock.create.return_value = member_to_add

    created = await pod_member_service.assign_member_to_pod(member_to_add, requester_id)

    assert created == member_to_add
    assert created.user_id == target_user_id
    assert created.user_email == "test+u@example.com"
    assert created.user_name is None
    create_arg = pod_member_repository_mock.create.await_args.args[0]
    assert create_arg.has_pending_events() is True
    events = create_arg.collect_events()
    assert len(events) == 1
    assert events[0].event_type == "pod.member.added"


@pytest.mark.asyncio
async def test_assign_member_not_found_pod_raises(
    pod_member_service: PodMemberService,
    pod_repository_mock: AsyncMock,
):
    pod_repository_mock.get.return_value = None

    with pytest.raises(PodNotFoundError):
        await pod_member_service.assign_member_to_pod(
            PodMemberEntity(
                pod_id=uuid4(),
                organization_member_id=uuid4(),
                role=PodRole.EDITOR,
            ),
            requester_user_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_assign_member_duplicate_raises_conflict(
    pod_member_service: PodMemberService,
    pod_repository_mock: AsyncMock,
    pod_member_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    pod = _make_pod(organization_id=uuid4(), user_id=requester_id)
    org_member = _make_org_member(
        user_id=requester_id,
        organization_id=pod.organization_id,
        role=OrganizationRole.ORG_OWNER,
    )

    pod_repository_mock.get.return_value = pod
    organization_repository_mock.get_member.return_value = org_member
    pod_member_repository_mock.get_by_pod_and_org_member.return_value = PodMemberEntity(
        pod_id=pod.id,
        organization_member_id=uuid4(),
        role=PodRole.EDITOR,
    )

    with pytest.raises(PodConflictError):
        await pod_member_service.assign_member_to_pod(
            PodMemberEntity(
                pod_id=pod.id,
                organization_member_id=uuid4(),
                role=PodRole.VIEWER,
            ),
            requester_user_id=requester_id,
        )


@pytest.mark.asyncio
async def test_assign_member_requires_admin_or_org_editor(
    pod_member_service: PodMemberService,
    pod_repository_mock: AsyncMock,
    pod_member_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    pod = _make_pod(organization_id=uuid4(), user_id=uuid4())
    requester_org_member = _make_org_member(
        user_id=requester_id,
        organization_id=pod.organization_id,
        role=OrganizationRole.ORG_MEMBER,
    )

    pod_repository_mock.get.return_value = pod
    organization_repository_mock.get_member.return_value = requester_org_member
    pod_member_repository_mock.get_by_pod_and_org_member.return_value = None

    with pytest.raises(PodAccessDeniedError):
        await pod_member_service.assign_member_to_pod(
            PodMemberEntity(
                pod_id=pod.id,
                organization_member_id=uuid4(),
                role=PodRole.VIEWER,
            ),
            requester_user_id=requester_id,
        )


@pytest.mark.asyncio
async def test_remove_member_owner_path_emits_event_and_deletes_entity(
    pod_member_service: PodMemberService,
    pod_repository_mock: AsyncMock,
    pod_member_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    target_user_id = uuid4()
    pod = _make_pod(organization_id=uuid4(), user_id=uuid4())

    member = PodMemberEntity(
        pod_id=pod.id,
        organization_member_id=uuid4(),
        role=PodRole.EDITOR,
        user_id=target_user_id,
    )

    pod_repository_mock.get.return_value = pod
    organization_repository_mock.get_member.return_value = _make_org_member(
        user_id=requester_id,
        organization_id=pod.organization_id,
        role=OrganizationRole.ORG_OWNER,
    )
    organization_repository_mock.get_member_by_id.return_value = _make_org_member(
        user_id=target_user_id,
        organization_id=pod.organization_id,
        role=OrganizationRole.ORG_MEMBER,
    )
    pod_member_repository_mock.delete_entity.return_value = True

    pod_member_repository_mock.get_by_pod_and_id.return_value = member

    result = await pod_member_service.remove_member_from_pod(
        pod.id,
        member.id,
        requester_id,
    )

    assert result is True
    deleted_arg = pod_member_repository_mock.delete_entity.await_args.args[0]
    events = deleted_arg.collect_events()
    assert len(events) == 1
    assert events[0].event_type == "pod.member.removed"


@pytest.mark.asyncio
async def test_remove_member_requires_permissions_for_non_owner(
    pod_member_service: PodMemberService,
    pod_repository_mock: AsyncMock,
    pod_member_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    pod = _make_pod(organization_id=uuid4(), user_id=uuid4())
    member = PodMemberEntity(
        pod_id=pod.id,
        organization_member_id=uuid4(),
        role=PodRole.VIEWER,
        user_id=uuid4(),
    )

    pod_member_repository_mock.get_by_pod_and_id.return_value = member
    pod_repository_mock.get.return_value = pod
    organization_repository_mock.get_member.return_value = _make_org_member(
        user_id=requester_id,
        organization_id=pod.organization_id,
        role=OrganizationRole.ORG_MEMBER,
    )
    pod_member_repository_mock.get_by_pod_and_org_member.return_value = None

    with pytest.raises(PodAccessDeniedError):
        await pod_member_service.remove_member_from_pod(
            pod.id,
            member.id,
            requester_id,
        )


@pytest.mark.asyncio
async def test_remove_member_not_found_raises(
    pod_member_service: PodMemberService,
    pod_member_repository_mock: AsyncMock,
):
    pod_member_repository_mock.get_by_pod_and_id.return_value = None

    with pytest.raises(PodMemberNotFoundError):
        await pod_member_service.remove_member_from_pod(uuid4(), uuid4(), uuid4())


@pytest.mark.asyncio
async def test_update_member_role_sets_role(
    pod_member_service: PodMemberService,
    pod_repository_mock: AsyncMock,
    pod_member_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    pod = _make_pod(organization_id=uuid4(), user_id=uuid4())
    member = PodMemberEntity(
        pod_id=pod.id,
        organization_member_id=uuid4(),
        role=PodRole.VIEWER,
        user_id=uuid4(),
        user_email="test+u@example.com",
    )

    pod_member_repository_mock.get_by_pod_and_id.return_value = member
    pod_repository_mock.get.return_value = pod
    organization_repository_mock.get_member.return_value = _make_org_member(
        user_id=requester_id,
        organization_id=pod.organization_id,
        role=OrganizationRole.ORG_OWNER,
    )
    organization_repository_mock.get_member_by_id.return_value = _make_org_member(
        user_id=member.user_id,
        organization_id=pod.organization_id,
        role=OrganizationRole.ORG_MEMBER,
    )
    pod_member_repository_mock.get_by_pod_and_org_member.return_value = PodMemberEntity(
        pod_id=pod.id,
        organization_member_id=uuid4(),
        role=PodRole.ADMIN,
    )
    pod_member_repository_mock.update.return_value = member

    updated = await pod_member_service.update_member_role(
        pod.id,
        member.id,
        PodRole.ADMIN,
        requester_id,
    )

    assert updated.role == PodRole.ADMIN
    assert updated.user_name is None


@pytest.mark.asyncio
async def test_get_pod_member_returns_member_by_user_id(
    pod_member_service: PodMemberService,
    pod_repository_mock: AsyncMock,
    pod_member_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    target_user_id = uuid4()
    pod = _make_pod(organization_id=uuid4(), user_id=uuid4())
    member = PodMemberEntity(
        pod_id=pod.id,
        organization_member_id=uuid4(),
        role=PodRole.VIEWER,
        user_id=target_user_id,
        user_email="test+u@example.com",
    )

    pod_repository_mock.get.return_value = pod
    organization_repository_mock.get_member.return_value = _make_org_member(
        user_id=requester_id,
        organization_id=pod.organization_id,
        role=OrganizationRole.ORG_OWNER,
    )
    pod_member_repository_mock.get_by_pod_and_user_id.return_value = member

    result = await pod_member_service.get_pod_member(
        pod.id,
        target_user_id,
        requester_id,
    )

    assert result == member


@pytest.mark.asyncio
async def test_get_pod_member_by_id_returns_member(
    pod_member_service: PodMemberService,
    pod_repository_mock: AsyncMock,
    pod_member_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    pod_member_id = uuid4()
    pod = _make_pod(organization_id=uuid4(), user_id=uuid4())
    member = PodMemberEntity(
        id=pod_member_id,
        pod_id=pod.id,
        organization_member_id=uuid4(),
        role=PodRole.VIEWER,
        user_id=uuid4(),
        user_email="test+u@example.com",
    )

    pod_repository_mock.get.return_value = pod
    organization_repository_mock.get_member.return_value = _make_org_member(
        user_id=requester_id,
        organization_id=pod.organization_id,
        role=OrganizationRole.ORG_OWNER,
    )
    pod_member_repository_mock.get_by_pod_and_id.return_value = member

    result = await pod_member_service.get_pod_member_by_id(
        pod.id,
        pod_member_id,
        requester_id,
    )

    assert result == member
    pod_member_repository_mock.get_by_pod_and_id.assert_awaited_once_with(
        pod.id,
        pod_member_id,
    )


@pytest.mark.asyncio
async def test_get_pod_member_by_email_returns_member(
    pod_member_service: PodMemberService,
    pod_repository_mock: AsyncMock,
    pod_member_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    pod = _make_pod(organization_id=uuid4(), user_id=uuid4())
    member = PodMemberEntity(
        pod_id=pod.id,
        organization_member_id=uuid4(),
        role=PodRole.VIEWER,
        user_id=uuid4(),
        user_email="test+u@example.com",
    )

    pod_repository_mock.get.return_value = pod
    organization_repository_mock.get_member.return_value = _make_org_member(
        user_id=requester_id,
        organization_id=pod.organization_id,
        role=OrganizationRole.ORG_OWNER,
    )
    pod_member_repository_mock.get_by_pod_and_user_email.return_value = member

    result = await pod_member_service.get_pod_member_by_user_email(
        pod.id,
        "test+u@example.com",
        requester_id,
    )

    assert result == member
    pod_member_repository_mock.get_by_pod_and_user_email.assert_awaited_once_with(
        pod.id,
        "test+u@example.com",
    )


@pytest.mark.asyncio
async def test_get_pod_member_raises_when_user_not_member(
    pod_member_service: PodMemberService,
    pod_repository_mock: AsyncMock,
    pod_member_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    target_user_id = uuid4()
    pod = _make_pod(organization_id=uuid4(), user_id=uuid4())

    pod_repository_mock.get.return_value = pod
    organization_repository_mock.get_member.return_value = _make_org_member(
        user_id=requester_id,
        organization_id=pod.organization_id,
        role=OrganizationRole.ORG_OWNER,
    )
    pod_member_repository_mock.get_by_pod_and_user_id.return_value = None

    with pytest.raises(PodMemberNotFoundError):
        await pod_member_service.get_pod_member(
            pod.id,
            target_user_id,
            requester_id,
        )


@pytest.mark.asyncio
async def test_get_pod_member_self_check_returns_not_found_without_org_membership(
    pod_member_service: PodMemberService,
    pod_repository_mock: AsyncMock,
    pod_member_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    pod = _make_pod(organization_id=uuid4(), user_id=uuid4())

    pod_repository_mock.get.return_value = pod
    pod_member_repository_mock.get_by_pod_and_user_id.return_value = None

    with pytest.raises(PodMemberNotFoundError):
        await pod_member_service.get_pod_member(
            pod.id,
            requester_id,
            requester_id,
        )

    organization_repository_mock.get_member.assert_not_called()


@pytest.mark.asyncio
async def test_list_pod_members_requires_membership(
    pod_member_service: PodMemberService,
    pod_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    pod = _make_pod(organization_id=uuid4(), user_id=uuid4())

    pod_repository_mock.get.return_value = pod
    organization_repository_mock.get_member.return_value = None

    with pytest.raises(PodAccessDeniedError):
        await pod_member_service.list_pod_members(pod.id, requester_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "org_role,pod_member_role,required_role,expected",
    [
        (OrganizationRole.ORG_OWNER, None, PodRole.ADMIN, True),
        (OrganizationRole.ORG_EDITOR, None, PodRole.ADMIN, True),
        (OrganizationRole.ORG_MEMBER, PodRole.ADMIN, PodRole.EDITOR, True),
        (OrganizationRole.ORG_MEMBER, PodRole.EDITOR, PodRole.USER, True),
        (OrganizationRole.ORG_MEMBER, PodRole.USER, PodRole.VIEWER, True),
        (OrganizationRole.ORG_MEMBER, PodRole.USER, PodRole.EDITOR, False),
        (OrganizationRole.ORG_MEMBER, PodRole.VIEWER, PodRole.ADMIN, False),
    ],
)
async def test_check_pod_permission_role_matrix(
    pod_member_service: PodMemberService,
    pod_repository_mock: AsyncMock,
    pod_member_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
    org_role: OrganizationRole,
    pod_member_role: PodRole | None,
    required_role: PodRole,
    expected: bool,
):
    user_id = uuid4()
    pod = _make_pod(organization_id=uuid4(), user_id=uuid4())

    pod_repository_mock.get.return_value = pod
    org_member = _make_org_member(
        user_id=user_id,
        organization_id=pod.organization_id,
        role=org_role,
    )
    organization_repository_mock.get_member.return_value = org_member

    if pod_member_role is None:
        pod_member_repository_mock.get_by_pod_and_org_member.return_value = None
    else:
        pod_member_repository_mock.get_by_pod_and_org_member.return_value = PodMemberEntity(
            pod_id=pod.id,
            organization_member_id=org_member.id,
            role=pod_member_role,
        )

    result = await pod_member_service.check_pod_permission(
        user_id, pod.id, required_role
    )

    assert result is expected
