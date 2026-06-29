from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.identity.domain.organization_entities import (
    OrganizationMemberEntity,
    OrganizationRole,
)
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.pod.domain.errors import PodAccessDeniedError, PodNotFoundError
from app.modules.pod.domain.pod_entities import (
    PodConfig,
    PodEntity,
    PodJoinPolicy,
    PodMemberEntity,
    PodRole,
    PodUpdateEntity,
)
from app.modules.pod.services.pod_service import PodService


def _make_user(email: str = "test+u@example.com") -> UserEntity:
    return UserEntity(email=email)


def _make_org_member(
    *,
    user_id,
    organization_id,
    role: OrganizationRole,
) -> OrganizationMemberEntity:
    return OrganizationMemberEntity(
        user_id=user_id,
        organization_id=organization_id,
        role=role,
        user=_make_user(),
    )


def _make_pod(*, organization_id, user_id) -> PodEntity:
    return PodEntity(
        name="Test Pod",
        description="pod",
        organization_id=organization_id,
        user_id=user_id,
    )


@pytest.mark.asyncio
async def test_create_pod_success_adds_admin_and_domain_event(
    pod_service: PodService,
    pod_repository_mock: AsyncMock,
    pod_member_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    creator_id = uuid4()
    organization_id = uuid4()
    org_member = _make_org_member(
        user_id=creator_id,
        organization_id=organization_id,
        role=OrganizationRole.ORG_OWNER,
    )
    pod = _make_pod(organization_id=organization_id, user_id=creator_id)

    organization_repository_mock.get_member.return_value = org_member
    pod_repository_mock.create.return_value = pod
    pod_member_repository_mock.create.return_value = PodMemberEntity(
        pod_id=pod.id,
        organization_member_id=org_member.id,
        role=PodRole.ADMIN,
    )

    created = await pod_service.create_pod(pod, creator_id)

    assert created == pod

    create_arg = pod_repository_mock.create.await_args.args[0]
    assert isinstance(create_arg, PodEntity)
    assert create_arg.has_pending_events() is True

    events = create_arg.collect_events()
    assert len(events) == 1
    assert events[0].event_type == "pod.created"

    pod_member_create_arg = pod_member_repository_mock.create.await_args.args[0]
    assert pod_member_create_arg.role == PodRole.ADMIN
    assert pod_member_create_arg.organization_member_id == org_member.id


@pytest.mark.asyncio
async def test_create_pod_requires_org_membership(
    pod_service: PodService,
    organization_repository_mock: AsyncMock,
):
    creator_id = uuid4()
    pod = _make_pod(organization_id=uuid4(), user_id=creator_id)

    organization_repository_mock.get_member.return_value = None

    with pytest.raises(PodAccessDeniedError):
        await pod_service.create_pod(pod, creator_id)


@pytest.mark.asyncio
async def test_get_pod_returns_none_when_not_found(
    pod_service: PodService,
    pod_repository_mock: AsyncMock,
):
    pod_repository_mock.get.return_value = None

    result = await pod_service.get_pod(uuid4(), uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_get_pod_requires_access(
    pod_service: PodService,
    pod_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    pod = _make_pod(organization_id=uuid4(), user_id=uuid4())
    pod_repository_mock.get.return_value = pod
    organization_repository_mock.get_member.return_value = None

    with pytest.raises(PodAccessDeniedError):
        await pod_service.get_pod(pod.id, requester_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("org_role", [OrganizationRole.ORG_OWNER, OrganizationRole.ORG_EDITOR])
async def test_get_pod_org_owner_or_editor_has_access(
    pod_service: PodService,
    pod_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
    org_role: OrganizationRole,
):
    requester_id = uuid4()
    pod = _make_pod(organization_id=uuid4(), user_id=uuid4())

    pod_repository_mock.get.return_value = pod
    organization_repository_mock.get_member.return_value = _make_org_member(
        user_id=requester_id,
        organization_id=pod.organization_id,
        role=org_role,
    )

    result = await pod_service.get_pod(pod.id, requester_id)

    assert result == pod


@pytest.mark.asyncio
async def test_get_pod_non_owner_requires_pod_access(
    pod_service: PodService,
    pod_repository_mock: AsyncMock,
    pod_member_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    pod = _make_pod(organization_id=uuid4(), user_id=uuid4())
    org_member = _make_org_member(
        user_id=requester_id,
        organization_id=pod.organization_id,
        role=OrganizationRole.ORG_MEMBER,
    )

    pod_repository_mock.get.return_value = pod
    organization_repository_mock.get_member.return_value = org_member
    pod_member_repository_mock.check_user_has_pod_access.return_value = False

    with pytest.raises(PodAccessDeniedError):
        await pod_service.get_pod(pod.id, requester_id)


@pytest.mark.asyncio
async def test_update_pod_not_found_raises(
    pod_service: PodService,
    pod_repository_mock: AsyncMock,
):
    pod_repository_mock.get.return_value = None

    with pytest.raises(PodNotFoundError):
        await pod_service.update_pod(uuid4(), PodUpdateEntity(name="x"), uuid4())


@pytest.mark.asyncio
async def test_update_pod_requires_admin_for_non_org_editor(
    pod_service: PodService,
    pod_repository_mock: AsyncMock,
    authorization_service_mock: AsyncMock,
):
    requester_id = uuid4()
    pod = _make_pod(organization_id=uuid4(), user_id=uuid4())

    pod_repository_mock.get.return_value = pod
    authorization_service_mock.require_user_action.side_effect = PodAccessDeniedError()

    with pytest.raises(PodAccessDeniedError):
        await pod_service.update_pod(
            pod.id,
            PodUpdateEntity(name="updated"),
            requester_id,
        )


@pytest.mark.asyncio
async def test_update_pod_merges_config_field_wise(
    pod_service: PodService,
    pod_repository_mock: AsyncMock,
):
    pod = PodEntity(
        name="Test Pod",
        organization_id=uuid4(),
        user_id=uuid4(),
        config=PodConfig(
            default_profile_id="profile-1",
            join_policy=PodJoinPolicy.INVITE_ONLY,
        ),
    )
    pod_repository_mock.get.return_value = pod
    pod_repository_mock.update.side_effect = lambda entity: entity

    ctx = AsyncMock()  # ctx.require is an awaitable no-op

    # Update only join_policy — default_profile_id must be preserved.
    result = await pod_service.update_pod(
        pod.id,
        PodUpdateEntity(config=PodConfig(join_policy=PodJoinPolicy.PUBLIC)),
        uuid4(),
        ctx=ctx,
    )

    assert result.config.join_policy == PodJoinPolicy.PUBLIC
    assert result.config.default_profile_id == "profile-1"


@pytest.mark.asyncio
async def test_update_pod_mirrors_default_runtime_profile_id(
    pod_service: PodService,
    pod_repository_mock: AsyncMock,
):
    pod = PodEntity(
        name="Test Pod",
        organization_id=uuid4(),
        user_id=uuid4(),
        config=PodConfig(default_profile_id="old-profile"),
    )
    pod_repository_mock.get.return_value = pod
    pod_repository_mock.update.side_effect = lambda entity: entity

    ctx = AsyncMock()

    # Set a full runtime (profile + model). The legacy default_profile_id must
    # be kept in sync so code still reading the old key resolves correctly.
    result = await pod_service.update_pod(
        pod.id,
        PodUpdateEntity(
            config=PodConfig(
                default_runtime=AgentRuntimeConfig(
                    profile_id="new-profile", model_name="claude-sonnet"
                )
            )
        ),
        uuid4(),
        ctx=ctx,
    )

    assert result.config.default_profile_id == "new-profile"  # mirrored
    runtime = result.config.resolved_default_runtime()
    assert runtime is not None
    assert runtime.profile_id == "new-profile"
    assert runtime.model_name == "claude-sonnet"


@pytest.mark.asyncio
async def test_delete_pod_marks_deleted_and_updates(
    pod_service: PodService,
    pod_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    pod = _make_pod(organization_id=uuid4(), user_id=uuid4())

    pod_repository_mock.get.return_value = pod
    organization_repository_mock.get_member.return_value = _make_org_member(
        user_id=requester_id,
        organization_id=pod.organization_id,
        role=OrganizationRole.ORG_OWNER,
    )
    pod_repository_mock.update.return_value = pod

    result = await pod_service.delete_pod(pod.id, requester_id)

    assert result is True
    update_arg = pod_repository_mock.update.await_args.args[0]
    assert update_arg.is_deleted is True
    assert update_arg.name.startswith("deleted-")

    events = update_arg.collect_events()
    assert len(events) == 1
    assert events[0].event_type == "pod.deleted"
    assert events[0].pod_id == pod.id
    assert events[0].organization_id == pod.organization_id


@pytest.mark.asyncio
async def test_list_pods_by_org_requires_membership(
    pod_service: PodService,
    organization_repository_mock: AsyncMock,
):
    organization_repository_mock.get_member.return_value = None

    with pytest.raises(PodAccessDeniedError):
        await pod_service.list_pods_by_organization(uuid4(), uuid4())


@pytest.mark.asyncio
async def test_list_pods_by_org_returns_only_member_pods(
    pod_service: PodService,
    pod_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    org_id = uuid4()
    org_member = _make_org_member(
        user_id=requester_id,
        organization_id=org_id,
        role=OrganizationRole.ORG_MEMBER,
    )
    organization_repository_mock.get_member.return_value = org_member
    pod_repository_mock.list_by_org_member.return_value = ([], None)

    await pod_service.list_pods_by_organization(org_id, requester_id, 10, None)

    pod_repository_mock.list_by_org_member.assert_awaited_once_with(
        org_id,
        org_member.id,
        10,
        None,
    )


@pytest.mark.asyncio
async def test_list_pods_by_org_owner_sees_all_pods(
    pod_service: PodService,
    pod_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    org_id = uuid4()
    org_member = _make_org_member(
        user_id=requester_id,
        organization_id=org_id,
        role=OrganizationRole.ORG_OWNER,
    )
    organization_repository_mock.get_member.return_value = org_member
    pod_repository_mock.list_by_org.return_value = ([], None)

    await pod_service.list_pods_by_organization(org_id, requester_id, 20, "cursor-1")

    pod_repository_mock.list_by_org.assert_awaited_once_with(org_id, 20, "cursor-1")
    pod_repository_mock.list_by_org_member.assert_not_called()


@pytest.mark.asyncio
async def test_list_pods_by_org_editor_sees_only_member_pods(
    pod_service: PodService,
    pod_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    requester_id = uuid4()
    org_id = uuid4()
    org_member = _make_org_member(
        user_id=requester_id,
        organization_id=org_id,
        role=OrganizationRole.ORG_EDITOR,
    )
    organization_repository_mock.get_member.return_value = org_member
    pod_repository_mock.list_by_org_member.return_value = ([], None)

    await pod_service.list_pods_by_organization(org_id, requester_id, 15, None)

    pod_repository_mock.list_by_org_member.assert_awaited_once_with(
        org_id,
        org_member.id,
        15,
        None,
    )
    pod_repository_mock.list_by_org.assert_not_called()
