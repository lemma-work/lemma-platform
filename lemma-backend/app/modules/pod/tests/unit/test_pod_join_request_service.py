from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.identity.domain.organization_entities import (
    OrganizationMemberEntity,
    OrganizationRole,
)
from app.modules.pod.domain.errors import PodAccessDeniedError, PodConflictError
from app.modules.pod.domain.events import PodJoinRequestedEvent
from app.modules.pod.domain.pod_entities import (
    PodConfig,
    PodEntity,
    PodJoinPolicy,
    PodJoinRequestEntity,
    PodJoinRequestStatus,
    PodMemberEntity,
    PodRole,
)
from app.modules.pod.services.pod_join_request_service import PodJoinRequestService


@pytest.mark.asyncio
async def test_request_join_is_idempotent_for_pending_request():
    pod_id = uuid4()
    org_id = uuid4()
    user_id = uuid4()

    pod_repo = AsyncMock()
    pod_repo.get.return_value = PodEntity(
        id=pod_id,
        user_id=uuid4(),
        organization_id=org_id,
        name="Pod",
    )

    join_repo = AsyncMock()
    pending = PodJoinRequestEntity(
        pod_id=pod_id,
        organization_id=org_id,
        user_id=user_id,
        status=PodJoinRequestStatus.PENDING,
    )
    join_repo.get_pending_by_pod_and_user.return_value = pending

    pod_member_repo = AsyncMock()
    org_repo = AsyncMock()
    org_repo.get_member.return_value = None

    service = PodJoinRequestService(
        pod_join_request_repository=join_repo,
        pod_member_repository=pod_member_repo,
        pod_repository=pod_repo,
        organization_repository=org_repo,
    )

    result, created_org = await service.request_join(pod_id, user_id)

    assert result == pending
    assert created_org is None
    join_repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_request_join_rejects_when_user_already_has_pod_access_via_org_role():
    pod_id = uuid4()
    org_id = uuid4()
    user_id = uuid4()

    pod_repo = AsyncMock()
    pod_repo.get.return_value = PodEntity(
        id=pod_id,
        user_id=uuid4(),
        organization_id=org_id,
        name="Pod",
    )

    join_repo = AsyncMock()
    pod_member_repo = AsyncMock()
    org_repo = AsyncMock()
    org_repo.get_member.return_value = type(
        "OrgMember",
        (),
        {
            "id": uuid4(),
            "role": OrganizationRole.ORG_OWNER,
            "organization_id": org_id,
            "user_id": user_id,
        },
    )()

    service = PodJoinRequestService(
        pod_join_request_repository=join_repo,
        pod_member_repository=pod_member_repo,
        pod_repository=pod_repo,
        organization_repository=org_repo,
    )

    with pytest.raises(PodConflictError):
        await service.request_join(pod_id, user_id)


@pytest.mark.asyncio
async def test_request_join_rejects_owner_with_expected_message():
    pod_id = uuid4()
    org_id = uuid4()
    user_id = uuid4()

    pod_repo = AsyncMock()
    pod_repo.get.return_value = PodEntity(
        id=pod_id,
        user_id=uuid4(),
        organization_id=org_id,
        name="Pod",
    )

    join_repo = AsyncMock()
    pod_member_repo = AsyncMock()
    org_repo = AsyncMock()
    org_repo.get_member.return_value = type(
        "OrgMember",
        (),
        {
            "id": uuid4(),
            "role": OrganizationRole.ORG_OWNER,
            "organization_id": org_id,
            "user_id": user_id,
        },
    )()

    service = PodJoinRequestService(
        pod_join_request_repository=join_repo,
        pod_member_repository=pod_member_repo,
        pod_repository=pod_repo,
        organization_repository=org_repo,
    )

    with pytest.raises(PodConflictError, match="Org owner has access to all pods by default"):
        await service.request_join(pod_id, user_id)


@pytest.mark.asyncio
async def test_request_join_allows_editor_if_not_pod_member():
    pod_id = uuid4()
    org_id = uuid4()
    user_id = uuid4()
    org_member_id = uuid4()

    pod_repo = AsyncMock()
    pod_repo.get.return_value = PodEntity(
        id=pod_id,
        user_id=uuid4(),
        organization_id=org_id,
        name="Pod",
    )

    created_request = PodJoinRequestEntity(
        pod_id=pod_id,
        organization_id=org_id,
        user_id=user_id,
        status=PodJoinRequestStatus.PENDING,
    )

    join_repo = AsyncMock()
    join_repo.get_pending_by_pod_and_user.return_value = None
    join_repo.create.return_value = created_request

    pod_member_repo = AsyncMock()
    pod_member_repo.get_by_pod_and_org_member.return_value = None

    org_repo = AsyncMock()
    org_repo.get_member.return_value = type(
        "OrgMember",
        (),
        {
            "id": org_member_id,
            "role": OrganizationRole.ORG_EDITOR,
            "organization_id": org_id,
            "user_id": user_id,
        },
    )()

    service = PodJoinRequestService(
        pod_join_request_repository=join_repo,
        pod_member_repository=pod_member_repo,
        pod_repository=pod_repo,
        organization_repository=org_repo,
    )

    result, created_org = await service.request_join(pod_id, user_id)

    assert result == created_request
    assert created_org is None
    pod_member_repo.get_by_pod_and_org_member.assert_awaited_once_with(
        pod_id, org_member_id
    )
    join_repo.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_approve_join_request_adds_org_and_pod_membership_when_missing():
    pod_id = uuid4()
    org_id = uuid4()
    requester_id = uuid4()
    target_user_id = uuid4()

    pod_repo = AsyncMock()
    pod_repo.get.return_value = PodEntity(
        id=pod_id,
        user_id=requester_id,
        organization_id=org_id,
        name="Pod",
    )

    join_request = PodJoinRequestEntity(
        id=uuid4(),
        pod_id=pod_id,
        organization_id=org_id,
        user_id=target_user_id,
        status=PodJoinRequestStatus.PENDING,
    )

    join_repo = AsyncMock()
    join_repo.get.return_value = join_request
    join_repo.update.side_effect = lambda entity: entity

    pod_member_repo = AsyncMock()
    pod_member_repo.get_by_pod_and_org_member.return_value = None

    requester_org_member = type(
        "OrgMember",
        (),
        {
            "id": uuid4(),
            "role": OrganizationRole.ORG_OWNER,
            "organization_id": org_id,
            "user_id": requester_id,
        },
    )()

    created_target_org_member = type(
        "OrgMember",
        (),
        {
            "id": uuid4(),
            "role": OrganizationRole.ORG_MEMBER,
            "organization_id": org_id,
            "user_id": target_user_id,
        },
    )()

    org_repo = AsyncMock()
    org_repo.get_member.side_effect = [requester_org_member, None]
    org_repo.add_member.return_value = created_target_org_member

    service = PodJoinRequestService(
        pod_join_request_repository=join_repo,
        pod_member_repository=pod_member_repo,
        pod_repository=pod_repo,
        organization_repository=org_repo,
    )

    result = await service.approve_join_request(
        pod_id,
        join_request.id,
        requester_id,
        org_role=OrganizationRole.ORG_MEMBER,
        pod_role=PodRole.USER,
    )

    assert result.status == PodJoinRequestStatus.APPROVED
    org_repo.add_member.assert_awaited_once()
    pod_member_repo.create.assert_awaited_once()


def _org_member(*, member_id, role, org_id, user_id):
    return OrganizationMemberEntity(
        id=member_id, user_id=user_id, organization_id=org_id, role=role
    )


@pytest.mark.asyncio
async def test_request_join_emits_join_requested_event():
    pod_id = uuid4()
    org_id = uuid4()
    user_id = uuid4()

    pod_repo = AsyncMock()
    pod_repo.get.return_value = PodEntity(
        id=pod_id, user_id=uuid4(), organization_id=org_id, name="Pod"
    )

    captured: list[PodJoinRequestEntity] = []

    def _create(entity):
        captured.append(entity)
        return entity

    join_repo = AsyncMock()
    join_repo.get_pending_by_pod_and_user.return_value = None
    join_repo.create.side_effect = _create

    pod_member_repo = AsyncMock()
    org_repo = AsyncMock()
    org_repo.get_member.return_value = None

    service = PodJoinRequestService(
        pod_join_request_repository=join_repo,
        pod_member_repository=pod_member_repo,
        pod_repository=pod_repo,
        organization_repository=org_repo,
    )

    await service.request_join(pod_id, user_id)

    assert captured
    events = captured[0].collect_events()
    assert any(isinstance(event, PodJoinRequestedEvent) for event in events)


def _pod_with_policy(pod_id, org_id, policy: PodJoinPolicy) -> PodEntity:
    return PodEntity(
        id=pod_id,
        user_id=uuid4(),
        organization_id=org_id,
        name="Pod",
        config=PodConfig(join_policy=policy),
    )


@pytest.mark.asyncio
async def test_join_pod_invite_only_is_rejected():
    pod_id, org_id, user_id = uuid4(), uuid4(), uuid4()
    pod_repo = AsyncMock()
    pod_repo.get.return_value = _pod_with_policy(pod_id, org_id, PodJoinPolicy.INVITE_ONLY)
    org_repo = AsyncMock()
    org_repo.get_member.return_value = None

    service = PodJoinRequestService(
        pod_join_request_repository=AsyncMock(),
        pod_member_repository=AsyncMock(),
        pod_repository=pod_repo,
        organization_repository=org_repo,
    )

    with pytest.raises(PodAccessDeniedError):
        await service.join_pod(pod_id, user_id)


@pytest.mark.asyncio
async def test_join_pod_org_members_requires_org_membership():
    pod_id, org_id, user_id = uuid4(), uuid4(), uuid4()
    pod_repo = AsyncMock()
    pod_repo.get.return_value = _pod_with_policy(pod_id, org_id, PodJoinPolicy.ORG_MEMBERS)
    org_repo = AsyncMock()
    org_repo.get_member.return_value = None

    service = PodJoinRequestService(
        pod_join_request_repository=AsyncMock(),
        pod_member_repository=AsyncMock(),
        pod_repository=pod_repo,
        organization_repository=org_repo,
    )

    with pytest.raises(PodAccessDeniedError):
        await service.join_pod(pod_id, user_id)


@pytest.mark.asyncio
async def test_join_pod_public_creates_org_and_pod_membership():
    pod_id, org_id, user_id, org_member_id = uuid4(), uuid4(), uuid4(), uuid4()
    pod_repo = AsyncMock()
    pod_repo.get.return_value = _pod_with_policy(pod_id, org_id, PodJoinPolicy.PUBLIC)

    org_repo = AsyncMock()
    org_repo.get_member.return_value = None
    created_org_member = _org_member(
        member_id=org_member_id,
        role=OrganizationRole.ORG_MEMBER,
        org_id=org_id,
        user_id=user_id,
    )
    org_repo.add_member.return_value = created_org_member

    pod_member_repo = AsyncMock()
    pod_member_repo.get_by_pod_and_org_member.return_value = None
    pod_member_repo.create.return_value = PodMemberEntity(
        pod_id=pod_id,
        organization_member_id=org_member_id,
        roles=[PodRole.USER.value],
    )

    service = PodJoinRequestService(
        pod_join_request_repository=AsyncMock(),
        pod_member_repository=pod_member_repo,
        pod_repository=pod_repo,
        organization_repository=org_repo,
    )

    member, new_org_member = await service.join_pod(pod_id, user_id)

    assert new_org_member is created_org_member
    assert member.roles == [PodRole.USER.value]
    org_repo.add_member.assert_awaited_once()
    pod_member_repo.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_join_pod_org_member_joins_without_new_org_member():
    pod_id, org_id, user_id, org_member_id = uuid4(), uuid4(), uuid4(), uuid4()
    pod_repo = AsyncMock()
    pod_repo.get.return_value = _pod_with_policy(pod_id, org_id, PodJoinPolicy.ORG_MEMBERS)

    org_repo = AsyncMock()
    org_repo.get_member.return_value = _org_member(
        member_id=org_member_id,
        role=OrganizationRole.ORG_MEMBER,
        org_id=org_id,
        user_id=user_id,
    )

    pod_member_repo = AsyncMock()
    pod_member_repo.get_by_pod_and_org_member.return_value = None
    pod_member_repo.create.return_value = PodMemberEntity(
        pod_id=pod_id,
        organization_member_id=org_member_id,
        roles=[PodRole.USER.value],
    )

    service = PodJoinRequestService(
        pod_join_request_repository=AsyncMock(),
        pod_member_repository=pod_member_repo,
        pod_repository=pod_repo,
        organization_repository=org_repo,
    )

    member, new_org_member = await service.join_pod(pod_id, user_id)

    assert new_org_member is None
    org_repo.add_member.assert_not_called()
    pod_member_repo.create.assert_awaited_once()
    assert member.roles == [PodRole.USER.value]


@pytest.mark.asyncio
async def test_request_join_public_pod_auto_joins_and_returns_approved():
    pod_id, org_id, user_id, org_member_id = uuid4(), uuid4(), uuid4(), uuid4()
    pod_repo = AsyncMock()
    pod_repo.get.return_value = _pod_with_policy(pod_id, org_id, PodJoinPolicy.PUBLIC)

    org_repo = AsyncMock()
    org_repo.get_member.return_value = None
    created_org_member = _org_member(
        member_id=org_member_id,
        role=OrganizationRole.ORG_MEMBER,
        org_id=org_id,
        user_id=user_id,
    )
    org_repo.add_member.return_value = created_org_member

    pod_member_repo = AsyncMock()
    pod_member_repo.get_by_pod_and_org_member.return_value = None
    pod_member_repo.create.return_value = PodMemberEntity(
        pod_id=pod_id,
        organization_member_id=org_member_id,
        roles=[PodRole.USER.value],
    )

    join_repo = AsyncMock()
    join_repo.get_pending_by_pod_and_user.return_value = None
    join_repo.create.side_effect = lambda entity: entity

    service = PodJoinRequestService(
        pod_join_request_repository=join_repo,
        pod_member_repository=pod_member_repo,
        pod_repository=pod_repo,
        organization_repository=org_repo,
    )

    request, created_org = await service.request_join(pod_id, user_id)

    assert request.status == PodJoinRequestStatus.APPROVED
    assert created_org is created_org_member
    org_repo.add_member.assert_awaited_once()
    pod_member_repo.create.assert_awaited_once()
    join_repo.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_request_join_org_members_pod_auto_joins_existing_member():
    pod_id, org_id, user_id, org_member_id = uuid4(), uuid4(), uuid4(), uuid4()
    pod_repo = AsyncMock()
    pod_repo.get.return_value = _pod_with_policy(pod_id, org_id, PodJoinPolicy.ORG_MEMBERS)

    org_repo = AsyncMock()
    org_repo.get_member.return_value = _org_member(
        member_id=org_member_id,
        role=OrganizationRole.ORG_MEMBER,
        org_id=org_id,
        user_id=user_id,
    )

    pod_member_repo = AsyncMock()
    pod_member_repo.get_by_pod_and_org_member.return_value = None
    pod_member_repo.create.return_value = PodMemberEntity(
        pod_id=pod_id,
        organization_member_id=org_member_id,
        roles=[PodRole.USER.value],
    )

    join_repo = AsyncMock()
    join_repo.get_pending_by_pod_and_user.return_value = None
    join_repo.create.side_effect = lambda entity: entity

    service = PodJoinRequestService(
        pod_join_request_repository=join_repo,
        pod_member_repository=pod_member_repo,
        pod_repository=pod_repo,
        organization_repository=org_repo,
    )

    request, created_org = await service.request_join(pod_id, user_id)

    assert request.status == PodJoinRequestStatus.APPROVED
    assert created_org is None
    org_repo.add_member.assert_not_called()
    pod_member_repo.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_request_join_org_members_pod_creates_pending_for_non_member():
    pod_id, org_id, user_id = uuid4(), uuid4(), uuid4()
    pod_repo = AsyncMock()
    pod_repo.get.return_value = _pod_with_policy(pod_id, org_id, PodJoinPolicy.ORG_MEMBERS)

    org_repo = AsyncMock()
    org_repo.get_member.return_value = None

    join_repo = AsyncMock()
    join_repo.get_pending_by_pod_and_user.return_value = None
    join_repo.create.side_effect = lambda entity: entity

    pod_member_repo = AsyncMock()

    service = PodJoinRequestService(
        pod_join_request_repository=join_repo,
        pod_member_repository=pod_member_repo,
        pod_repository=pod_repo,
        organization_repository=org_repo,
    )

    request, created_org = await service.request_join(pod_id, user_id)

    assert request.status == PodJoinRequestStatus.PENDING
    assert created_org is None
    org_repo.add_member.assert_not_called()
    pod_member_repo.create.assert_not_called()
