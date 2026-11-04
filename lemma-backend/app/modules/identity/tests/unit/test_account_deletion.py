"""Deleting one's own account: the person goes, the work they shared stays.

Driven through in-memory stand-ins for the identity repositories and the pod
port, which keep what they are told, so each test asserts on the state a
deletion leaves -- who is still a member, which organizations and pods are
gone, what the account row says -- rather than on which calls were made.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.core.domain.errors import DomainError
from app.modules.identity.contracts.account_deletion import AccountDeletionRefusal
from app.modules.identity.domain.events import UserDeletedEvent
from app.modules.identity.domain.organization_entities import (
    OrganizationEntity,
    OrganizationInvitationEntity,
    OrganizationInvitationStatus,
    OrganizationJoinPolicy,
    OrganizationMemberEntity,
    OrganizationRole,
)
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.services.account_deletion import (
    DELETED_ORGANIZATION_NAME,
    AccountDeletionService,
)

pytestmark = pytest.mark.unit


class Users:
    def __init__(self, *users: UserEntity) -> None:
        self.rows = {user.id: user for user in users}
        self.forgotten_emails: list[str] = []
        self.events: list[object] = []

    async def get(self, id: UUID) -> UserEntity | None:
        found = self.rows.get(id)
        return found.model_copy() if found else None

    async def save_erased(
        self, entity: UserEntity, *, previous_email: str
    ) -> UserEntity:
        self.events.extend(entity.collect_events())
        self.rows[entity.id] = entity
        self.forgotten_emails.append(previous_email)
        return entity


@dataclass
class Organizations:
    organizations: dict[UUID, OrganizationEntity] = field(default_factory=dict)
    members: dict[UUID, OrganizationMemberEntity] = field(default_factory=dict)
    invitations: dict[UUID, OrganizationInvitationEntity] = field(default_factory=dict)
    locked: list[UUID] = field(default_factory=list)

    def add(self, name: str) -> OrganizationEntity:
        organization = OrganizationEntity(name=name, slug=name.lower())
        self.organizations[organization.id] = organization
        return organization

    def join(
        self, organization: OrganizationEntity, user_id: UUID, role: OrganizationRole
    ) -> OrganizationMemberEntity:
        member = OrganizationMemberEntity(
            user_id=user_id, organization_id=organization.id, role=role
        )
        self.members[member.id] = member
        return member

    def invite(self, organization: OrganizationEntity, email: str) -> None:
        invitation = OrganizationInvitationEntity(
            email=email,
            organization_id=organization.id,
            role=OrganizationRole.ORG_MEMBER,
        )
        self.invitations[invitation.id] = invitation

    def members_of(self, organization: OrganizationEntity) -> set[UUID]:
        return {
            member.user_id
            for member in self.members.values()
            if member.organization_id == organization.id
        }

    async def list_user_memberships(
        self, user_id: UUID
    ) -> list[OrganizationMemberEntity]:
        return sorted(
            (m.model_copy() for m in self.members.values() if m.user_id == user_id),
            key=lambda member: member.organization_id,
        )

    async def lock_seats(self, organization_id: UUID) -> None:
        self.locked.append(organization_id)

    async def count_members(self, organization_id: UUID) -> int:
        return sum(
            1 for m in self.members.values() if m.organization_id == organization_id
        )

    async def count_members_with_role_for_update(
        self, organization_id: UUID, role: OrganizationRole
    ) -> int:
        return sum(
            1
            for m in self.members.values()
            if m.organization_id == organization_id and m.role == role
        )

    async def get(self, id: UUID) -> OrganizationEntity | None:
        found = self.organizations.get(id)
        return found.model_copy() if found else None

    async def get_many(self, ids: Sequence[UUID]) -> dict[UUID, OrganizationEntity]:
        return {id: self.organizations[id] for id in ids if id in self.organizations}

    async def update(self, entity: OrganizationEntity) -> OrganizationEntity:
        self.organizations[entity.id] = entity
        return entity

    async def update_member(
        self, entity: OrganizationMemberEntity
    ) -> OrganizationMemberEntity:
        self.members[entity.id] = entity
        return entity

    async def delete_member(self, member_id: UUID) -> bool:
        return self.members.pop(member_id, None) is not None

    async def list_organization_invitations(
        self,
        organization_id: UUID,
        status: OrganizationInvitationStatus | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[OrganizationInvitationEntity], str | None]:
        del cursor
        found = [
            i.model_copy()
            for i in self.invitations.values()
            if i.organization_id == organization_id and i.status == status
        ]
        return found[:limit], None

    async def update_invitation(
        self, entity: OrganizationInvitationEntity
    ) -> OrganizationInvitationEntity:
        self.invitations[entity.id] = entity
        return entity


@dataclass
class Pods:
    """Which pods each organization holds, and who has left which organization's."""

    by_organization: dict[UUID, list[UUID]] = field(default_factory=dict)
    deleted: list[UUID] = field(default_factory=list)
    departures: list[UUID] = field(default_factory=list)
    deleted_by: list[UUID] = field(default_factory=list)

    async def leave_organization_pods(
        self, *, organization_member_id: UUID, user_id: UUID
    ) -> list[UUID]:
        del user_id
        self.departures.append(organization_member_id)
        return []

    async def delete_organization_pods(
        self, *, organization_id: UUID, requester_user_id: UUID
    ) -> list[UUID]:
        gone = self.by_organization.pop(organization_id, [])
        self.deleted.extend(gone)
        self.deleted_by.append(requester_user_id)
        return gone


@dataclass
class Guard:
    answer: AccountDeletionRefusal | None = None
    asked_about: list[list[UUID]] = field(default_factory=list)

    async def refusal(
        self, *, user_id: UUID, deleted_organization_ids: Sequence[UUID]
    ) -> AccountDeletionRefusal | None:
        del user_id
        self.asked_about.append(list(deleted_organization_ids))
        return self.answer


@dataclass
class World:
    person: UserEntity
    users: Users
    organizations: Organizations
    pods: Pods

    def service(
        self, *, guard: Guard | None = None, deployment_bills: bool = False
    ) -> AccountDeletionService:
        return AccountDeletionService(
            user_repository=self.users,  # type: ignore[arg-type]
            organization_repository=self.organizations,  # type: ignore[arg-type]
            pod_membership_port=self.pods,  # type: ignore[arg-type]
            billing_guard=guard,
            deployment_bills=deployment_bills,
        )

    @property
    def account(self) -> UserEntity:
        return self.users.rows[self.person.id]


@pytest.fixture
def world() -> World:
    person = UserEntity(
        email="test+leaving@example.com",
        first_name="Asha",
        last_name="Rao",
        mobile_number="+14155552671",
        mobile_verified_at=datetime.now(timezone.utc),
        timezone="Asia/Kolkata",
        country="IN",
    )
    return World(person, Users(person), Organizations(), Pods())


async def _refused(call) -> DomainError:
    with pytest.raises(DomainError) as refused:
        await call
    return refused.value


async def test_a_confirmation_that_is_not_the_accounts_address_deletes_nothing(
    world: World,
):
    workspace = world.organizations.add("Solo")
    world.organizations.join(workspace, world.person.id, OrganizationRole.ORG_OWNER)

    for typed in ("someone-else@example.com", "DELETE", ""):
        refused = await _refused(
            world.service().delete_account(world.person.id, confirm_email=typed)
        )
        assert refused.status_code == 400
        assert refused.code == "ACCOUNT_DELETION_CONFIRMATION_MISMATCH"

    assert world.account.is_deleted is False
    assert world.organizations.members_of(workspace) == {world.person.id}


async def test_the_address_is_matched_whatever_its_case(world: World):
    await world.service().delete_account(
        world.person.id, confirm_email="  Test+Leaving@Example.COM "
    )

    assert world.account.is_deleted is True


async def test_the_account_becomes_a_tombstone_that_names_nobody(world: World):
    await world.service().delete_account(
        world.person.id, confirm_email="test+leaving@example.com"
    )

    account = world.account
    assert account.id == world.person.id
    assert account.is_deleted is True and account.is_active is False
    assert account.deactivation_reason == "ACCOUNT_DELETED"
    assert str(account.email) == f"deleted-{world.person.id}@deleted.example"
    assert (account.first_name, account.last_name) == ("Deleted", "user")
    assert account.mobile_number is None and account.mobile_verified_at is None
    assert (account.timezone, account.country, account.date_of_birth) == (
        None,
        None,
        None,
    )
    assert account.preferences is None
    assert world.users.forgotten_emails == ["test+leaving@example.com"]
    # One event: what the account answered to is let go by its subscribers.
    assert [type(event) for event in world.users.events] == [UserDeletedEvent]


async def test_an_organization_others_work_in_is_left_and_keeps_its_pods(world: World):
    team = world.organizations.add("Team")
    colleague = uuid4()
    world.organizations.join(team, colleague, OrganizationRole.ORG_OWNER)
    mine = world.organizations.join(team, world.person.id, OrganizationRole.ORG_OWNER)
    world.pods.by_organization[team.id] = [uuid4()]

    outcome = await world.service().delete_account(
        world.person.id, confirm_email="test+leaving@example.com"
    )

    assert outcome.organizations_left == (team.id,)
    assert outcome.organizations_deleted == ()
    assert world.organizations.members_of(team) == {colleague}
    # Out of each of its pods through the ordinary departure, and nothing more.
    assert world.pods.departures == [mine.id]
    assert world.pods.deleted == []
    assert world.organizations.organizations[team.id].name == "Team"


async def test_an_organization_nobody_else_is_in_goes_with_the_account(world: World):
    workspace = world.organizations.add("Asha's workspace")
    workspace.email_domain = "example.com"
    workspace.join_policy = OrganizationJoinPolicy.EMAIL_DOMAIN
    world.organizations.join(workspace, world.person.id, OrganizationRole.ORG_OWNER)
    world.organizations.invite(workspace, "test+invited@example.com")
    pods = [uuid4(), uuid4()]
    world.pods.by_organization[workspace.id] = list(pods)

    outcome = await world.service().delete_account(
        world.person.id, confirm_email="test+leaving@example.com"
    )

    assert outcome.organizations_deleted == (workspace.id,)
    assert outcome.pods_deleted == 2
    assert world.pods.deleted == pods
    assert world.organizations.members_of(workspace) == set()
    gone = world.organizations.organizations[workspace.id]
    # Nothing it was called, and no way back in.
    assert gone.name == DELETED_ORGANIZATION_NAME
    assert gone.slug == f"deleted-{workspace.id.hex}"
    assert gone.email_domain is None
    assert gone.join_policy == OrganizationJoinPolicy.INVITE_ONLY
    assert {i.status for i in world.organizations.invitations.values()} == {
        OrganizationInvitationStatus.REVOKED
    }


async def test_a_last_member_who_is_not_its_owner_can_still_take_it_with_them(
    world: World,
):
    orphaned = world.organizations.add("Orphaned")
    world.organizations.join(orphaned, world.person.id, OrganizationRole.ORG_MEMBER)
    world.pods.by_organization[orphaned.id] = [uuid4()]

    outcome = await world.service().delete_account(
        world.person.id, confirm_email="test+leaving@example.com"
    )

    assert outcome.pods_deleted == 1
    assert world.pods.deleted_by == [world.person.id]
    assert world.organizations.members_of(orphaned) == set()


async def test_the_only_owner_of_an_organization_others_are_in_is_refused(
    world: World,
):
    acme = world.organizations.add("Acme")
    world.organizations.join(acme, world.person.id, OrganizationRole.ORG_OWNER)
    world.organizations.join(acme, uuid4(), OrganizationRole.ORG_EDITOR)
    workspace = world.organizations.add("Solo")
    world.organizations.join(workspace, world.person.id, OrganizationRole.ORG_OWNER)
    world.pods.by_organization[workspace.id] = [uuid4()]

    refused = await _refused(
        world.service().delete_account(
            world.person.id, confirm_email="test+leaving@example.com"
        )
    )

    assert refused.status_code == 409
    assert refused.code == "ACCOUNT_DELETION_SOLE_OWNER"
    assert "Make someone else an owner of Acme first" in refused.message
    assert refused.details == {"organizations": [{"id": str(acme.id), "name": "Acme"}]}
    # Refused whole: not even the organization it could have deleted.
    assert world.account.is_deleted is False
    assert world.pods.deleted == []
    assert world.person.id in world.organizations.members_of(acme)
    assert world.person.id in world.organizations.members_of(workspace)


async def test_every_organization_that_would_be_stranded_is_named(world: World):
    for name in ("Acme", "Beta"):
        organization = world.organizations.add(name)
        world.organizations.join(
            organization, world.person.id, OrganizationRole.ORG_OWNER
        )
        world.organizations.join(organization, uuid4(), OrganizationRole.ORG_MEMBER)

    refused = await _refused(
        world.service().delete_account(
            world.person.id, confirm_email="test+leaving@example.com"
        )
    )

    assert {o["name"] for o in refused.details["organizations"]} == {"Acme", "Beta"}
    assert " and " in refused.message


async def test_what_billing_refuses_is_said_in_its_words_with_nothing_changed(
    world: World,
):
    workspace = world.organizations.add("Solo")
    world.organizations.join(workspace, world.person.id, OrganizationRole.ORG_OWNER)
    guard = Guard(
        AccountDeletionRefusal(
            code="ACCOUNT_DELETION_ACTIVE_SUBSCRIPTION",
            message="Cancel your Pro plan first.",
        )
    )

    refused = await _refused(
        world.service(guard=guard, deployment_bills=True).delete_account(
            world.person.id, confirm_email="test+leaving@example.com"
        )
    )

    assert (refused.status_code, refused.code, refused.message) == (
        409,
        "ACCOUNT_DELETION_ACTIVE_SUBSCRIPTION",
        "Cancel your Pro plan first.",
    )
    # The guard weighs what would go with the account, not only the person.
    assert guard.asked_about == [[workspace.id]]
    assert world.account.is_deleted is False
    assert world.organizations.members_of(workspace) == {world.person.id}


async def test_a_deployment_that_bills_but_cannot_ask_billing_refuses(world: World):
    refused = await _refused(
        world.service(deployment_bills=True).delete_account(
            world.person.id, confirm_email="test+leaving@example.com"
        )
    )

    assert (refused.status_code, refused.code) == (503, "ACCOUNT_DELETION_UNAVAILABLE")
    assert world.account.is_deleted is False


async def test_a_deployment_with_nothing_to_bill_needs_no_guard(world: World):
    await world.service(deployment_bills=False).delete_account(
        world.person.id, confirm_email="test+leaving@example.com"
    )

    assert world.account.is_deleted is True


async def test_deleting_an_account_that_is_already_gone_reports_success_again(
    world: World,
):
    service = world.service()
    await service.delete_account(
        world.person.id, confirm_email="test+leaving@example.com"
    )
    world.users.events.clear()

    again = await service.delete_account(
        world.person.id, confirm_email="test+leaving@example.com"
    )

    assert again.already_deleted is True
    assert world.users.events == []


def test_every_module_holding_something_of_the_persons_lets_go_with_the_account():
    """The sign-in, paired computers, MCP clients, proved chat identities and
    the name on their lines: each is let go by work the deletion writes."""
    from app.core.infrastructure.work import facts
    from app.core.registry.installed import OSS_MODULES
    from app.modules.identity.contracts.account_facts import AccountDeleted

    for module in OSS_MODULES:
        if module.register_work is not None:
            module.register_work()

    assert {s.kind for s in facts.subscribers(AccountDeleted)} >= {
        "identity.account_deleted",
        "agent.hosts.account_deleted",
        "mcp_access.account_deleted",
        "surfaces.account_deleted",
        "channels.account_deleted",
    }


def test_only_the_person_themselves_can_delete_their_account():
    """Not an agent acting for them, and not a pod app's page they have open."""
    from app.core.api.actor_policy import app_may_call, workload_may_call

    assert workload_may_call("DELETE", "/users/me") is False
    assert app_may_call("DELETE", "/users/me") is False


class _Session:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    async def revoke_session(self) -> None:
        self.order.append("this session")


async def test_the_caller_is_signed_out_after_every_other_session():
    """Everywhere first: that drops the cached standing, which is what refuses
    an access token that has not expired yet."""
    from app.modules.identity.infrastructure.supertokens_auth.account_removal import (
        sign_out_after_deletion,
    )

    order: list[str] = []

    async def everywhere(user_id: UUID) -> None:
        del user_id
        order.append("everywhere")

    done = await sign_out_after_deletion(_Session(order), uuid4(), remove=everywhere)

    assert done is True
    assert order == ["everywhere", "this session"]


async def test_a_sign_in_service_that_is_down_leaves_the_deletion_standing():
    """The account is already gone; its sign-in ends later, from the work the
    deletion wrote, and the person is not told the deletion failed."""
    from redis.exceptions import ConnectionError as RedisConnectionError
    from supertokens_python.exceptions import GeneralError

    from app.modules.identity.infrastructure.supertokens_auth.account_removal import (
        sign_out_after_deletion,
    )

    for outage in (
        GeneralError("No SuperTokens core available to query"),
        RedisConnectionError("Connection refused"),
    ):
        order: list[str] = []

        async def unreachable(user_id: UUID, outage: Exception = outage) -> None:
            del user_id
            raise outage

        done = await sign_out_after_deletion(
            _Session(order), uuid4(), remove=unreachable
        )

        assert done is False
        assert order == []
