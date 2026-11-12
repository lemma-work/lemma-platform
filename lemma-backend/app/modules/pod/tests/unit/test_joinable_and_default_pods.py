"""Finding the pods open to you, and the pods that take in everyone who arrives.

Three rules, held without a database: who is offered pods to join, which
configs may take in new organization members, and what joining the
organization does once a pod is set to take them in. The SQL that picks the
pods is proved against Postgres in ``tests/e2e/test_joinable_and_default_pods_e2e.py``.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.modules.identity.contracts.organization_facts import OrganizationMemberAdded
from app.modules.identity.domain.organization_entities import (
    OrganizationMemberEntity,
    OrganizationRole,
)
from app.modules.pod.domain.errors import (
    PodAccessDeniedError,
    PodAutoJoinNeedsOpenPolicyError,
)
from app.modules.pod.domain.pod_entities import (
    JoinablePod,
    PodConfig,
    PodEntity,
    PodJoinPolicy,
    PodMemberEntity,
    PodRole,
    PodUpdateEntity,
)
from app.modules.pod.services.pod_join_request_service import PodJoinRequestService
from app.modules.pod.services.pod_service import PodService

pytestmark = pytest.mark.unit


class _Organization:
    """The organization memberships the services read, held in memory."""

    def __init__(self, *members: OrganizationMemberEntity) -> None:
        self._members = {(m.user_id, m.organization_id): m for m in members}

    async def get_member(
        self, user_id: UUID, organization_id: UUID
    ) -> OrganizationMemberEntity | None:
        return self._members.get((user_id, organization_id))


class _PodMembers:
    """Pod memberships, as the join paths make and look them up."""

    def __init__(self) -> None:
        self.rows: list[PodMemberEntity] = []

    def pods_of(self, organization_member_id: UUID) -> set[UUID]:
        return {
            row.pod_id
            for row in self.rows
            if row.organization_member_id == organization_member_id
        }

    async def get_by_pod_and_org_member(
        self, pod_id: UUID, org_member_id: UUID
    ) -> PodMemberEntity | None:
        return next(
            (
                row
                for row in self.rows
                if row.pod_id == pod_id and row.organization_member_id == org_member_id
            ),
            None,
        )

    async def create(self, entity: PodMemberEntity) -> PodMemberEntity:
        self.rows.append(entity)
        return entity


class _Pods:
    """The pod rows `PodService` reads and writes."""

    def __init__(self, *pods: PodEntity) -> None:
        self._pods = {pod.id: pod for pod in pods}

    def stored(self, pod_id: UUID) -> PodEntity:
        return self._pods[pod_id]

    async def create(self, entity: PodEntity) -> PodEntity:
        self._pods[entity.id] = entity
        return entity

    async def get(self, id: UUID) -> PodEntity | None:
        return self._pods.get(id)

    async def lock(self, id: UUID) -> None:
        del id

    async def update(self, entity: PodEntity) -> PodEntity:
        self._pods[entity.id] = entity
        return entity


class _OpenPods:
    """``OpenPodsPort``, keeping its contract.

    ``list_auto_join_pod_ids`` answers the organization's default pods the
    member is not in, by id, a page at a time -- so a service that reads it
    wrongly fails here as it would against SQL.
    """

    def __init__(
        self,
        *,
        members: _PodMembers | None = None,
        default_pods: dict[UUID, list[UUID]] | None = None,
        joinable: list[JoinablePod] | None = None,
    ) -> None:
        self._members = members or _PodMembers()
        self._default_pods = default_pods or {}
        self._joinable = joinable or []
        self.default_pod_reads = 0
        self.joinable_asked_for: list[tuple[UUID, UUID, int, str | None]] = []

    async def list_joinable(
        self,
        organization_id: UUID,
        organization_member_id: UUID,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[JoinablePod], str | None]:
        self.joinable_asked_for.append(
            (organization_id, organization_member_id, limit, cursor)
        )
        return self._joinable[:limit], None

    async def list_auto_join_pod_ids(
        self,
        organization_id: UUID,
        organization_member_id: UUID,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[UUID], str | None]:
        self.default_pod_reads += 1
        joined = self._members.pods_of(organization_member_id)
        after = UUID(cursor) if cursor else None
        candidates = sorted(
            pod_id
            for pod_id in self._default_pods.get(organization_id, [])
            if pod_id not in joined and (after is None or pod_id > after)
        )
        page = candidates[: limit + 1]
        next_cursor = str(page[limit - 1]) if len(page) > limit else None
        return page[:limit], next_cursor


class _Context:
    """Records the permissions a request was asked to hold; holds them all."""

    def __init__(self) -> None:
        self.required: list[str] = []

    async def require(self, permission_id: str, resource: object = None) -> None:
        del resource
        self.required.append(permission_id)


def _member(
    organization_id: UUID, role: OrganizationRole = OrganizationRole.ORG_MEMBER
) -> OrganizationMemberEntity:
    return OrganizationMemberEntity(
        user_id=uuid4(), organization_id=organization_id, role=role
    )


def _pod(organization_id: UUID, config: PodConfig) -> PodEntity:
    return PodEntity(
        name="Launch room",
        organization_id=organization_id,
        user_id=uuid4(),
        config=config,
    )


def _pod_service(
    pods: _Pods, organization: _Organization, open_pods: _OpenPods | None = None
) -> PodService:
    return PodService(
        pod_repository=pods,
        pod_member_repository=_PodMembers(),
        organization_repository=organization,
        open_pods=open_pods or _OpenPods(),
    )


# -- Who is offered pods to join ---------------------------------------------


async def test_someone_outside_the_organization_is_refused_its_joinable_pods():
    organization_id = uuid4()
    service = _pod_service(_Pods(), _Organization())

    with pytest.raises(PodAccessDeniedError) as refused:
        await service.list_joinable_pods(organization_id, uuid4())

    assert refused.value.status_code == 403


async def test_an_organization_owner_is_offered_no_pods_to_join():
    """They open every pod already, and joining one is refused to them."""
    organization_id = uuid4()
    owner = _member(organization_id, OrganizationRole.ORG_OWNER)
    open_pods = _OpenPods(
        joinable=[
            JoinablePod(
                id=uuid4(),
                name="Open room",
                join_policy=PodJoinPolicy.ORG_MEMBERS,
                member_count=1,
            )
        ]
    )
    service = _pod_service(_Pods(), _Organization(owner), open_pods)

    offered = await service.list_joinable_pods(organization_id, owner.user_id)

    assert offered == ([], None)
    assert open_pods.joinable_asked_for == []


@pytest.mark.parametrize(
    "role", [OrganizationRole.ORG_MEMBER, OrganizationRole.ORG_EDITOR]
)
async def test_a_member_is_offered_the_pods_open_to_their_own_membership(
    role: OrganizationRole,
):
    organization_id = uuid4()
    member = _member(organization_id, role)
    open_room = JoinablePod(
        id=uuid4(),
        name="Open room",
        description="Where launches are planned",
        join_policy=PodJoinPolicy.PUBLIC,
        member_count=4,
    )
    open_pods = _OpenPods(joinable=[open_room])
    service = _pod_service(_Pods(), _Organization(member), open_pods)

    offered, cursor = await service.list_joinable_pods(
        organization_id, member.user_id, 10, None
    )

    assert list(offered) == [open_room]
    assert cursor is None
    # Keyed on their organization membership, which is what "not in it" is
    # measured against.
    assert open_pods.joinable_asked_for == [(organization_id, member.id, 10, None)]


async def test_a_service_wired_without_open_pods_fails_rather_than_offering_none():
    """ "Nothing to join" from a service that cannot look would read as true."""
    organization_id = uuid4()
    member = _member(organization_id)
    service = PodService(
        pod_repository=_Pods(),
        pod_member_repository=_PodMembers(),
        organization_repository=_Organization(member),
    )

    with pytest.raises(RuntimeError, match="open_pods"):
        await service.list_joinable_pods(organization_id, member.user_id)


# -- Which configs may take in new members -----------------------------------


async def test_creating_an_invite_only_pod_that_takes_in_new_members_is_refused():
    organization_id = uuid4()
    creator = _member(organization_id)
    pods = _Pods()
    service = _pod_service(pods, _Organization(creator))
    # No join policy given: a new pod is invite-only.
    pod = _pod(organization_id, PodConfig(auto_join_new_members=True))

    with pytest.raises(PodAutoJoinNeedsOpenPolicyError) as refused:
        await service.create_pod(pod, creator.user_id)

    assert refused.value.status_code == 422
    assert refused.value.code == "POD_AUTO_JOIN_NEEDS_OPEN_POLICY"
    with pytest.raises(KeyError):
        pods.stored(pod.id)


@pytest.mark.parametrize("policy", [PodJoinPolicy.ORG_MEMBERS, PodJoinPolicy.PUBLIC])
async def test_a_pod_open_to_members_is_created_taking_in_new_members(
    policy: PodJoinPolicy,
):
    organization_id = uuid4()
    creator = _member(organization_id)
    pods = _Pods()
    service = _pod_service(pods, _Organization(creator))
    pod = _pod(
        organization_id, PodConfig(join_policy=policy, auto_join_new_members=True)
    )

    created = await service.create_pod(pod, creator.user_id)

    assert pods.stored(created.id).config.auto_join_new_members is True
    assert pods.stored(created.id).config.join_policy == policy


async def test_turning_on_auto_join_for_an_invite_only_pod_is_refused():
    organization_id = uuid4()
    pod = _pod(organization_id, PodConfig())
    pods = _Pods(pod)
    service = _pod_service(pods, _Organization())

    with pytest.raises(PodAutoJoinNeedsOpenPolicyError):
        await service.update_pod(
            pod.id,
            PodUpdateEntity(config=PodConfig(auto_join_new_members=True)),
            uuid4(),
            ctx=_Context(),
        )

    assert pods.stored(pod.id).config.auto_join_new_members is False


async def test_closing_a_pod_that_takes_in_new_members_needs_auto_join_off_too():
    """Judged on the merged config: closing the pod alone would leave it
    invite-only and taking in everyone, which is the same conflict."""
    organization_id = uuid4()
    pod = _pod(
        organization_id,
        PodConfig(join_policy=PodJoinPolicy.ORG_MEMBERS, auto_join_new_members=True),
    )
    pods = _Pods(pod)
    service = _pod_service(pods, _Organization())

    with pytest.raises(PodAutoJoinNeedsOpenPolicyError):
        await service.update_pod(
            pod.id,
            PodUpdateEntity(config=PodConfig(join_policy=PodJoinPolicy.INVITE_ONLY)),
            uuid4(),
            ctx=_Context(),
        )
    assert pods.stored(pod.id).config.join_policy == PodJoinPolicy.ORG_MEMBERS

    await service.update_pod(
        pod.id,
        PodUpdateEntity(
            config=PodConfig(
                join_policy=PodJoinPolicy.INVITE_ONLY, auto_join_new_members=False
            )
        ),
        uuid4(),
        ctx=_Context(),
    )

    closed = pods.stored(pod.id).config
    assert (closed.join_policy, closed.auto_join_new_members) == (
        PodJoinPolicy.INVITE_ONLY,
        False,
    )


async def test_auto_join_turns_off_alone_and_leaves_the_join_policy():
    organization_id = uuid4()
    pod = _pod(
        organization_id,
        PodConfig(join_policy=PodJoinPolicy.ORG_MEMBERS, auto_join_new_members=True),
    )
    pods = _Pods(pod)
    service = _pod_service(pods, _Organization())

    await service.update_pod(
        pod.id,
        PodUpdateEntity(
            config=PodConfig.model_validate({"auto_join_new_members": False})
        ),
        uuid4(),
        ctx=_Context(),
    )

    stored = pods.stored(pod.id).config
    assert stored.auto_join_new_members is False
    assert stored.join_policy == PodJoinPolicy.ORG_MEMBERS


async def test_changing_auto_join_needs_the_authority_the_join_policy_needs():
    """Deciding who joins without asking is membership management, like the
    join policy: `pod.update` alone may not change it."""
    organization_id = uuid4()
    pod = _pod(organization_id, PodConfig(join_policy=PodJoinPolicy.ORG_MEMBERS))
    pods = _Pods(pod)
    service = _pod_service(pods, _Organization())

    turned_on = _Context()
    await service.update_pod(
        pod.id,
        PodUpdateEntity(
            config=PodConfig.model_validate({"auto_join_new_members": True})
        ),
        uuid4(),
        ctx=turned_on,
    )
    assert turned_on.required == ["pod.update", "pod.member.manage"]

    # Sent back unchanged, as a client writing the whole config does: renaming
    # the pod is not membership management because the config came along.
    unchanged = _Context()
    await service.update_pod(
        pod.id,
        PodUpdateEntity(
            name="Renamed room", config=pods.stored(pod.id).config.model_copy()
        ),
        uuid4(),
        ctx=unchanged,
    )
    assert unchanged.required == ["pod.update"]


# -- Joining the organization ------------------------------------------------


class _NoJoinRequests:
    """Joining by arriving asks nobody: any call here fails the test."""


def _join_request_service(
    open_pods: _OpenPods, members: _PodMembers, organization: _Organization
) -> PodJoinRequestService:
    return PodJoinRequestService(
        pod_join_request_repository=_NoJoinRequests(),  # type: ignore[arg-type]
        pod_member_repository=members,
        pod_repository=_Pods(),
        organization_repository=organization,
        open_pods=open_pods,
    )


async def test_a_new_member_joins_every_default_pod_as_a_pod_user():
    organization_id = uuid4()
    newcomer = _member(organization_id)
    default_pods = [uuid4(), uuid4()]
    members = _PodMembers()
    open_pods = _OpenPods(members=members, default_pods={organization_id: default_pods})
    service = _join_request_service(open_pods, members, _Organization(newcomer))

    joined = await service.join_default_pods(
        organization_id=organization_id, user_id=newcomer.user_id
    )

    assert sorted(joined) == sorted(default_pods)
    assert sorted((row.pod_id, tuple(row.roles)) for row in members.rows) == sorted(
        (pod_id, (PodRole.USER.value,)) for pod_id in default_pods
    )
    assert {row.organization_member_id for row in members.rows} == {newcomer.id}


async def test_running_the_default_pod_join_again_adds_no_second_membership():
    """A FAST item whose lease expires runs again."""
    organization_id = uuid4()
    newcomer = _member(organization_id)
    members = _PodMembers()
    open_pods = _OpenPods(members=members, default_pods={organization_id: [uuid4()]})
    service = _join_request_service(open_pods, members, _Organization(newcomer))

    await service.join_default_pods(
        organization_id=organization_id, user_id=newcomer.user_id
    )
    again = await service.join_default_pods(
        organization_id=organization_id, user_id=newcomer.user_id
    )

    assert again == []
    assert len(members.rows) == 1


async def test_an_organization_owner_is_put_in_no_default_pod():
    organization_id = uuid4()
    owner = _member(organization_id, OrganizationRole.ORG_OWNER)
    members = _PodMembers()
    open_pods = _OpenPods(members=members, default_pods={organization_id: [uuid4()]})
    service = _join_request_service(open_pods, members, _Organization(owner))

    joined = await service.join_default_pods(
        organization_id=organization_id, user_id=owner.user_id
    )

    assert joined == []
    assert members.rows == []


async def test_someone_who_left_before_the_join_ran_is_put_in_no_pod():
    organization_id = uuid4()
    members = _PodMembers()
    open_pods = _OpenPods(members=members, default_pods={organization_id: [uuid4()]})
    service = _join_request_service(open_pods, members, _Organization())

    joined = await service.join_default_pods(
        organization_id=organization_id, user_id=uuid4()
    )

    assert joined == []
    assert members.rows == []


async def test_default_pods_are_joined_a_page_at_a_time():
    organization_id = uuid4()
    newcomer = _member(organization_id)
    default_pods = [uuid4() for _ in range(150)]
    members = _PodMembers()
    open_pods = _OpenPods(members=members, default_pods={organization_id: default_pods})
    service = _join_request_service(open_pods, members, _Organization(newcomer))

    joined = await service.join_default_pods(
        organization_id=organization_id, user_id=newcomer.user_id
    )

    assert sorted(joined) == sorted(default_pods)
    assert open_pods.default_pod_reads == 2


def test_joining_an_organization_queues_one_fast_default_pod_join_per_person():
    """Wired through the pod module's registration, under the organization."""
    from app.core.infrastructure.work import (
        WorkClass,
        WorkMode,
        facts,
        org_namespace,
        registry,
    )
    from app.modules.pod.events.auto_join_work import POD_AUTO_JOIN_ORG_MEMBER_ADDED
    from app.modules.pod.module import module as pod_module

    assert pod_module.register_work is not None
    pod_module.register_work()

    [subscription] = [
        s
        for s in facts.subscribers(OrganizationMemberAdded)
        if s.kind == POD_AUTO_JOIN_ORG_MEMBER_ADDED
    ]
    spec = registry.get(POD_AUTO_JOIN_ORG_MEMBER_ADDED)
    organization_id, user_id = uuid4(), uuid4()
    fact = OrganizationMemberAdded(organization_id=organization_id, user_id=user_id)

    assert (spec.cls, spec.mode) == (WorkClass.EVENTS, WorkMode.FAST)
    assert subscription.dedup is not None
    assert subscription.dedup(fact) == f"pod-auto-join:{organization_id}:{user_id}"
    assert fact.work_namespace() == org_namespace(organization_id)
