"""The three doors into a first workspace, against a real database.

The order is the whole point. Someone arriving from `ada@acme.com` when Acme is
already in Lemma must land *in* Acme, not in a private organization of one --
and the only way to know that holds is to put two people from the same domain
through it and look at where the second one ends up.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.models import RoleAssignmentModel, RoleModel
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.identity.domain.organization_entities import (
    OrganizationEntity,
    OrganizationJoinPolicy,
)
from app.modules.identity.infrastructure.adapters.pod_membership_adapter import (
    SqlAlchemyPodMembershipAdapter,
)
from app.modules.identity.infrastructure.organization_repositories import (
    OrganizationRepository,
)
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.identity.domain.workspace_names import (
    organization_name_from_work_domain,
)
from app.modules.identity.services.first_workspace import ensure_first_workspace
from app.modules.identity.services.organization_service import OrganizationService
from app.modules.identity.infrastructure.models.organization_models import (
    OrganizationMember,
)
from app.modules.pod.contracts.personal_workspace import create_named_workspace
from app.modules.pod.infrastructure.models.pod_models import Pod, PodMember

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def _org_roles(
    session: AsyncSession, organization_id: UUID, user_id: UUID
) -> set[str]:
    """The organization roles assigned to this member: what checks read."""
    return set(
        await session.scalars(
            select(RoleModel.name)
            .join(RoleAssignmentModel, RoleAssignmentModel.role_id == RoleModel.id)
            .join(
                OrganizationMember,
                OrganizationMember.id == RoleAssignmentModel.principal_id,
            )
            .where(
                RoleAssignmentModel.principal_type == "ORG_MEMBER",
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == user_id,
                RoleModel.pod_id.is_(None),
            )
        )
    )


async def _pod_roles(session: AsyncSession, pod_id: UUID, user_id: UUID) -> set[str]:
    """The pod roles assigned to this person's membership of the pod."""
    return set(
        await session.scalars(
            select(RoleModel.name)
            .join(RoleAssignmentModel, RoleAssignmentModel.role_id == RoleModel.id)
            .join(PodMember, PodMember.id == RoleAssignmentModel.principal_id)
            .join(
                OrganizationMember,
                OrganizationMember.id == PodMember.organization_member_id,
            )
            .where(
                RoleAssignmentModel.principal_type == "POD_MEMBER",
                PodMember.pod_id == pod_id,
                OrganizationMember.user_id == user_id,
            )
        )
    )


def _organization_service(uow) -> OrganizationService:
    return OrganizationService(
        organization_repository=OrganizationRepository(uow),
        user_repository=UserRepository(uow),
        invitation_accept_base_url="https://app.example.test",
        pod_membership_port=SqlAlchemyPodMembershipAdapter(uow),
    )


async def test_a_personal_address_gets_a_workspace_of_ones_own_and_its_lemma(
    signup_user, db_session
):
    """Nothing to name it after but the person, and nobody else to let in."""
    signed_up = await signup_user(email=f"ada-{uuid4().hex[:8]}@gmail.com")
    user_id = UUID(signed_up["id"])
    uow = SqlAlchemyUnitOfWork(db_session)

    workspace = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=user_id,
        email=signed_up["email"],
        full_name="Ada Lovelace",
        with_pod=False,
    )
    await uow.commit()

    assert workspace.entry == "new_org"
    assert workspace.organization_name == "Ada's workspace"
    assert workspace.organization_open_to_domain is None
    organization = await OrganizationRepository(uow).get(workspace.organization_id)
    assert organization is not None
    # A personal address opens no door for anyone else.
    assert organization.join_policy == OrganizationJoinPolicy.INVITE_ONLY
    assert organization.email_domain is None
    # A workspace of one has one Lemma, and it is theirs alone: private, not
    # the organization's shared Lemma, so nobody invited later is put in it
    # and it carries no mark for everyone.
    assert workspace.pod_name == "Personal"
    assert workspace.pod_created
    assert workspace.organization_pod_id is None
    lemma = await db_session.get(Pod, workspace.pod_id)
    assert lemma is not None
    assert lemma.config.get("join_policy", "INVITE_ONLY") == "INVITE_ONLY"
    assert lemma.config.get("auto_join_new_members", False) is False


@pytest.mark.parametrize(
    "local_part,expected",
    [
        # No name given: the one the address spells.
        ("grace.hopper-{tag}", "Grace's workspace"),
        # An address that spells no name: nobody's, so "My".
        ("x9-{tag}", "My workspace"),
    ],
)
async def test_a_workspace_with_no_name_given_is_named_from_the_address(
    signup_user, db_session, local_part, expected
):
    signed_up = await signup_user(
        email=f"{local_part.format(tag=uuid4().hex[:8])}@gmail.com"
    )
    uow = SqlAlchemyUnitOfWork(db_session)

    workspace = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=UUID(signed_up["id"]),
        email=signed_up["email"],
    )
    await uow.commit()

    assert workspace.organization_name == expected
    assert workspace.pod_name == "Personal"


async def test_a_work_address_names_the_company_and_opens_the_door(
    signup_user, db_session
):
    # A fresh domain per run: an organization claims a domain exclusively, so a
    # fixed one would collide with the row a previous run left behind. `.com`
    # rather than `.test`, which signup rejects as not a real address.
    domain = f"first-workspace-{uuid4().hex[:8]}.com"
    signed_up = await signup_user(email=f"ada@{domain}")
    user_id = UUID(signed_up["id"])
    uow = SqlAlchemyUnitOfWork(db_session)

    workspace = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=user_id,
        email=signed_up["email"],
        full_name="Ada Lovelace",
    )
    await uow.commit()

    organization = await OrganizationRepository(uow).get(workspace.organization_id)
    assert organization is not None
    # Named after the company; the exact spelling is pinned in the unit tests.
    assert organization.name == organization_name_from_work_domain(domain)
    # So the next colleague from this domain joins rather than fragments.
    assert organization.join_policy == OrganizationJoinPolicy.EMAIL_DOMAIN
    assert organization.email_domain == domain
    assert workspace.organization_open_to_domain == domain

    # Made with its own Lemma, called what the organization is, open to all of
    # it and joined by everyone who arrives later.
    assert workspace.pod_id == workspace.organization_pod_id
    lemma = await db_session.get(Pod, workspace.pod_id)
    assert lemma is not None
    assert lemma.name == organization.name
    assert lemma.config["join_policy"] == "ORG_MEMBERS"
    assert lemma.config["auto_join_new_members"] is True
    assert await _pod_roles(db_session, workspace.pod_id, user_id) == {"POD_ADMIN"}
    assert await _org_roles(db_session, workspace.organization_id, user_id) == {
        "ORG_OWNER"
    }


async def test_the_second_person_from_a_domain_joins_instead_of_fragmenting(
    signup_user, db_session
):
    """The failure this whole ordering exists to prevent."""
    domain = f"acme-{uuid4().hex[:8]}.com"
    uow = SqlAlchemyUnitOfWork(db_session)

    first = await signup_user(email=f"ada@{domain}")
    first_workspace = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=UUID(first["id"]),
        email=first["email"],
        full_name="Ada Lovelace",
    )
    await uow.commit()

    second = await signup_user(email=f"grace@{domain}")
    second_workspace = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=UUID(second["id"]),
        email=second["email"],
        full_name="Grace Hopper",
    )

    assert second_workspace.entry == "domain_join"
    assert second_workspace.organization_id == first_workspace.organization_id
    # Into the first one's Lemma, not a pod of their own beside it.
    assert second_workspace.pod_id == first_workspace.organization_pod_id
    assert second_workspace.organization_pod_id == first_workspace.organization_pod_id
    assert second_workspace.assistant_id == first_workspace.assistant_id
    assert not second_workspace.pod_created
    # Before anything has committed, so before the arrival handler can have
    # run: the membership is this call's own, as a pod user, with an
    # organization role that grants what a member holds.
    assert await _pod_roles(
        db_session, second_workspace.pod_id, UUID(second["id"])
    ) == {"POD_USER"}
    assert await _org_roles(
        db_session, second_workspace.organization_id, UUID(second["id"])
    ) == {"ORG_MEMBER"}
    await uow.commit()


async def test_starting_their_own_skips_the_company_organization(
    signup_user, db_session
):
    """ "Start your own organization instead": theirs, by invitation only."""
    domain = f"own-{uuid4().hex[:8]}.com"
    uow = SqlAlchemyUnitOfWork(db_session)
    first = await signup_user(email=f"ada@{domain}")
    company = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=UUID(first["id"]),
        email=first["email"],
    )
    await uow.commit()

    second = await signup_user(email=f"grace@{domain}")
    own = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=UUID(second["id"]),
        email=second["email"],
        full_name="Grace Hopper",
        own_organization=True,
    )
    await uow.commit()

    assert own.entry == "new_org"
    assert own.organization_id != company.organization_id
    assert own.organization_name == "Grace's workspace"
    assert own.organization_open_to_domain is None
    assert own.pod_name == "Personal"
    organization = await OrganizationRepository(uow).get(own.organization_id)
    assert organization is not None
    assert organization.join_policy == OrganizationJoinPolicy.INVITE_ONLY


async def test_a_workspace_nobody_else_can_reach_is_one_persons(
    signup_user, db_session
):
    """`team=False`, which a local deployment sends: a work address still makes
    a workspace of one, claiming no domain nobody else could arrive from."""
    domain = f"local-{uuid4().hex[:8]}.com"
    signed_up = await signup_user(email=f"ada@{domain}")
    uow = SqlAlchemyUnitOfWork(db_session)

    workspace = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=UUID(signed_up["id"]),
        email=signed_up["email"],
        full_name="Ada Lovelace",
        team=False,
    )
    await uow.commit()

    assert workspace.entry == "new_org"
    assert workspace.organization_name == "Ada's workspace"
    assert workspace.organization_open_to_domain is None
    assert workspace.pod_name == "Personal"
    organization = await OrganizationRepository(uow).get(workspace.organization_id)
    assert organization is not None
    assert organization.join_policy == OrganizationJoinPolicy.INVITE_ONLY
    assert organization.email_domain is None


async def test_a_domain_another_organization_holds_is_not_claimed_twice(
    signup_user, db_session, monkeypatch
):
    """A company organization this call did not see -- made by a request it
    did not wait for -- holds the domain. The new one is made anyway, by
    invitation only, rather than failing the sign-up."""
    domain = f"race-{uuid4().hex[:8]}.com"
    uow = SqlAlchemyUnitOfWork(db_session)
    first = await signup_user(email=f"ada@{domain}")
    company = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=UUID(first["id"]),
        email=first["email"],
    )
    await uow.commit()

    second = await signup_user(email=f"grace@{domain}")
    service = _organization_service(uow)

    async def nothing_suggested(*args: object, **kwargs: object):
        return [], None

    monkeypatch.setattr(service, "list_suggested_organizations", nothing_suggested)
    arrived = await ensure_first_workspace(
        uow,
        organization_service=service,
        user_id=UUID(second["id"]),
        email=second["email"],
    )
    await uow.commit()

    assert arrived.entry == "new_org"
    assert arrived.organization_id != company.organization_id
    assert arrived.organization_open_to_domain is None
    organization = await OrganizationRepository(uow).get(arrived.organization_id)
    assert organization is not None
    assert organization.join_policy == OrganizationJoinPolicy.INVITE_ONLY
    # Still a company's organization, so its Lemma is named after it.
    assert arrived.pod_name == organization.name


async def test_the_organization_someone_arrived_through_is_tried_first(
    signup_user, db_session
):
    """Somebody inside a company's own Slack is not a candidate for a private org.

    A personal address would otherwise get one of its own, which is the wrong
    answer for a colleague standing in their employer's workspace.
    """
    uow = SqlAlchemyUnitOfWork(db_session)
    service = _organization_service(uow)

    host = await signup_user(email=f"owner-{uuid4().hex[:8]}@gmail.com")
    installing = await service.create_organization(
        OrganizationEntity(
            name=f"Installing Org {uuid4().hex[:6]}",
            slug="",
            join_policy=OrganizationJoinPolicy.PUBLIC,
        ),
        UUID(host["id"]),
        resolve_name_conflicts=True,
    )
    await uow.commit()

    arriving = await signup_user(email=f"ada-{uuid4().hex[:8]}@gmail.com")
    workspace = await ensure_first_workspace(
        uow,
        organization_service=service,
        user_id=UUID(arriving["id"]),
        email=arriving["email"],
        arrived_through_organization_id=installing.id,
    )
    await uow.commit()

    assert workspace.entry == "surface_join"
    assert workspace.organization_id == installing.id
    assert workspace.pod_id is not None
    assert workspace.assistant_id is not None
    assert workspace.pod_created


async def test_an_invite_only_organization_still_refuses_a_surface_arrival(
    signup_user, db_session
):
    """A reachable surface is not an open organization.

    Verification does not grant membership or select a different organization.
    """
    uow = SqlAlchemyUnitOfWork(db_session)
    service = _organization_service(uow)

    host = await signup_user(email=f"owner-{uuid4().hex[:8]}@gmail.com")
    closed = await service.create_organization(
        OrganizationEntity(
            name=f"Closed Org {uuid4().hex[:6]}",
            slug="",
            join_policy=OrganizationJoinPolicy.INVITE_ONLY,
        ),
        UUID(host["id"]),
        resolve_name_conflicts=True,
    )
    await uow.commit()

    arriving = await signup_user(email=f"ada-{uuid4().hex[:8]}@gmail.com")
    workspace = await ensure_first_workspace(
        uow,
        organization_service=service,
        user_id=UUID(arriving["id"]),
        email=arriving["email"],
        arrived_through_organization_id=closed.id,
    )
    await uow.commit()

    assert workspace.status == "organization_access_required"
    assert workspace.organization_id == closed.id
    assert workspace.pod_id is None
    assert workspace.assistant_id is None
    assert not workspace.organization_created


async def test_an_organization_older_than_its_lemma_still_gets_a_private_pod(
    signup_user, db_session
):
    """An organization made before every one came with a Lemma has nowhere
    shared to land. A first workspace there is the private pod it used to be,
    when asked for one, and only once."""
    signed_up = await signup_user(email=f"ada-{uuid4().hex[:8]}@gmail.com")
    user_id = UUID(signed_up["id"])
    uow = SqlAlchemyUnitOfWork(db_session)
    service = _organization_service(uow)

    existing = await service.create_organization(
        OrganizationEntity(name=f"Already Here {uuid4().hex[:6]}", slug=""),
        user_id,
        resolve_name_conflicts=True,
    )
    await uow.commit()

    asked_for_none = await ensure_first_workspace(
        uow,
        organization_service=service,
        user_id=user_id,
        email=signed_up["email"],
        with_pod=False,
    )
    assert asked_for_none.pod_id is None
    assert asked_for_none.organization_pod_id is None

    workspace = await ensure_first_workspace(
        uow,
        organization_service=service,
        user_id=user_id,
        email=signed_up["email"],
    )
    await uow.commit()

    assert workspace.entry == "existing"
    assert workspace.organization_id == existing.id
    assert workspace.pod_id is not None
    assert workspace.assistant_id is not None
    assert workspace.organization_pod_id is None
    assert workspace.pod_created

    again = await ensure_first_workspace(
        uow,
        organization_service=service,
        user_id=user_id,
        email=signed_up["email"],
    )
    await uow.commit()
    assert again.pod_id == workspace.pod_id
    assert not again.pod_created


async def test_a_repeat_call_makes_nothing_new(signup_user, db_session):
    """Not a second organization, nor a private pod beside the shared Lemma
    (which the private-pod lookup does not count, since it is not private)."""
    signed_up = await signup_user(email=f"ada-{uuid4().hex[:8]}@gmail.com")
    user_id = UUID(signed_up["id"])
    uow = SqlAlchemyUnitOfWork(db_session)

    first = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=user_id,
        email=signed_up["email"],
        full_name="Ada Lovelace",
    )
    await uow.commit()
    second = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=user_id,
        email=signed_up["email"],
        full_name="Ada Lovelace",
    )
    await uow.commit()

    assert second.entry == "existing"
    assert (second.organization_id, second.pod_id) == (
        first.organization_id,
        first.pod_id,
    )
    assert not second.organization_created and not second.pod_created
    pods = await db_session.scalar(
        select(func.count(Pod.id)).where(
            Pod.organization_id == first.organization_id, Pod.is_deleted.is_(False)
        )
    )
    assert pods == 1


async def test_a_member_who_left_the_shared_lemma_is_not_put_back(
    signup_user, db_session
):
    """Asking again where somebody works -- a second chat sign-up, the Slack
    install page -- answers with where they are. It does not undo their
    leaving the organization's Lemma, which only a newcomer is added to."""
    domain = f"acme-{uuid4().hex[:8]}.com"
    uow = SqlAlchemyUnitOfWork(db_session)
    first = await signup_user(email=f"ada@{domain}")
    made = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=UUID(first["id"]),
        email=first["email"],
        full_name="Ada Lovelace",
    )
    await uow.commit()
    second = await signup_user(email=f"grace@{domain}")
    second_id = UUID(second["id"])
    joined = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=second_id,
        email=second["email"],
        full_name="Grace Hopper",
    )
    await uow.commit()
    assert joined.pod_id == made.organization_pod_id

    member = await db_session.scalar(
        select(PodMember)
        .join(
            OrganizationMember,
            OrganizationMember.id == PodMember.organization_member_id,
        )
        .where(
            PodMember.pod_id == made.organization_pod_id,
            OrganizationMember.user_id == second_id,
        )
    )
    assert member is not None
    await db_session.delete(member)
    await db_session.commit()

    again = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=second_id,
        email=second["email"],
        full_name="Grace Hopper",
        with_pod=False,
    )
    await uow.commit()

    assert again.entry == "existing"
    assert again.pod_id is None
    # Still named, so it can be offered back among the Lemmas to join.
    assert again.organization_pod_id == made.organization_pod_id
    assert await _pod_roles(db_session, made.organization_pod_id, second_id) == set()


async def test_unverified_email_cannot_claim_a_company_domain(signup_user, db_session):
    from app.modules.identity.infrastructure.models.user_models import User

    signed_up = await signup_user(email=f"unverified@company-{uuid4().hex}.com")
    user_id = UUID(signed_up["id"])
    user = await db_session.get(User, user_id)
    user.is_verified = False
    await db_session.commit()
    uow = SqlAlchemyUnitOfWork(db_session)
    workspace = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=user_id,
        email="forged@another-company.com",
    )
    await uow.commit()
    organization = await OrganizationRepository(uow).get(workspace.organization_id)
    assert organization.join_policy == OrganizationJoinPolicy.INVITE_ONLY
    assert organization.email_domain is None
    assert workspace.pod_id and workspace.assistant_id


async def _owner_with_invitation(signup_user, uow, invitee_email: str):
    from app.modules.identity.domain.organization_entities import (
        OrganizationInvitationEntity,
        OrganizationRole,
    )

    # A company's organization, so it has a Lemma everyone in it shares: a
    # workspace of one keeps its person's own Lemma private.
    owner = await signup_user(email=f"owner@inv-{uuid4().hex[:8]}.com")
    owned = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=UUID(owner["id"]),
        email=owner["email"],
        full_name="Grace Hopper",
    )
    # A pod other than the organization's Lemma, so landing on the one an
    # invitation names is told apart from landing in the shared one.
    room = await create_named_workspace(
        uow,
        organization_id=owned.organization_id,
        owner_user_id=UUID(owner["id"]),
        name="Launch Room",
    )
    invitation = OrganizationInvitationEntity(
        email=invitee_email,
        organization_id=owned.organization_id,
        role=OrganizationRole.ORG_MEMBER,
        pod_id=room.pod_id,
        pod_role="POD_USER",
    )
    await OrganizationRepository(uow).add_invitation(invitation)
    await uow.commit()
    return (
        replace(owned, pod_id=room.pod_id, assistant_id=room.assistant_id),
        invitation,
    )


async def test_a_pending_pod_invitation_is_where_they_land(signup_user, db_session):
    """Invited to a pod, then signed up another way: they join it, nothing new."""
    uow = SqlAlchemyUnitOfWork(db_session)
    invitee_email = f"ada-{uuid4().hex[:8]}@gmail.com"
    owned, invitation = await _owner_with_invitation(signup_user, uow, invitee_email)
    invitee = await signup_user(email=invitee_email)

    workspace = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=UUID(invitee["id"]),
        email=invitee["email"],
        full_name="Ada Lovelace",
    )
    await uow.commit()

    assert workspace.entry == "invitation"
    assert workspace.organization_id == owned.organization_id
    assert workspace.pod_id == owned.pod_id
    assert workspace.assistant_id == owned.assistant_id
    assert workspace.pod_created is False
    assert workspace.organization_pod_id == owned.organization_pod_id
    # New to the organization, so already in its own Lemma as well, before
    # anything has committed for the arrival handler to act on.
    assert await _pod_roles(
        db_session, owned.organization_pod_id, UUID(invitee["id"])
    ) == {"POD_USER"}
    assert await _org_roles(db_session, owned.organization_id, UUID(invitee["id"])) == {
        "ORG_MEMBER"
    }
    stored = await OrganizationRepository(uow).get_invitation_by_id(invitation.id)
    assert stored is not None and stored.status.value == "ACCEPTED"


async def test_an_invitation_to_a_deleted_pod_does_not_block_signup(
    signup_user, db_session
):
    """Refused whole and skipped: they arrive the way they would have anyway."""
    from app.modules.pod.infrastructure.models.pod_models import Pod

    uow = SqlAlchemyUnitOfWork(db_session)
    invitee_email = f"ada-{uuid4().hex[:8]}@gmail.com"
    owned, _ = await _owner_with_invitation(signup_user, uow, invitee_email)
    pod = await db_session.get(Pod, owned.pod_id)
    pod.is_deleted = True
    await uow.commit()
    invitee = await signup_user(email=invitee_email)

    workspace = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=UUID(invitee["id"]),
        email=invitee["email"],
        full_name="Ada Lovelace",
    )

    assert workspace.entry == "new_org"
    assert workspace.pod_id not in (None, owned.pod_id)


async def test_an_existing_member_invited_to_a_pod_gets_the_pod(
    signup_user, db_session
):
    """Already in the organization is no reason to refuse the pod."""
    uow = SqlAlchemyUnitOfWork(db_session)
    invitee_email = f"ada-{uuid4().hex[:8]}@gmail.com"
    owned, invitation = await _owner_with_invitation(signup_user, uow, invitee_email)
    invitee = await signup_user(email=invitee_email)
    service = _organization_service(uow)
    from app.modules.identity.domain.organization_entities import (
        OrganizationMemberEntity,
        OrganizationRole,
    )

    await OrganizationRepository(uow).add_member(
        OrganizationMemberEntity(
            user_id=UUID(invitee["id"]),
            organization_id=owned.organization_id,
            role=OrganizationRole.ORG_MEMBER,
        )
    )

    await service.accept_invitation(invitation.id, UUID(invitee["id"]))
    await uow.commit()

    adapter = SqlAlchemyPodMembershipAdapter(uow)
    assert owned.pod_id is not None
    assert await adapter.is_pod_member(pod_id=owned.pod_id, user_id=UUID(invitee["id"]))
