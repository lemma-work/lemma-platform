"""The three doors into a first workspace, against a real database.

The order is the whole point. Someone arriving from `ada@acme.com` when Acme is
already in Lemma must land *in* Acme, not in a private organization of one --
and the only way to know that holds is to put two people from the same domain
through it and look at where the second one ends up.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.events.message_bus import get_message_bus
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

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


def _organization_service(uow) -> OrganizationService:
    message_bus = get_message_bus()
    return OrganizationService(
        organization_repository=OrganizationRepository(uow, message_bus=message_bus),
        user_repository=UserRepository(uow, message_bus=message_bus),
        invitation_accept_base_url="https://app.example.test",
        pod_membership_port=SqlAlchemyPodMembershipAdapter(uow),
    )


async def test_a_personal_address_gets_a_generated_workspace_and_a_pod(
    signup_user, db_session
):
    """Nothing to name it after, so the name is hashed from the address."""
    signed_up = await signup_user(email=f"ada-{uuid4().hex[:8]}@gmail.com")
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

    assert workspace.entry == "new_org"
    assert workspace.pod_id is not None

    organization = await OrganizationRepository(uow).get(workspace.organization_id)
    assert organization is not None
    # A personal address opens no door for anyone else.
    assert organization.join_policy == OrganizationJoinPolicy.INVITE_ONLY
    assert organization.email_domain is None


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
    # Named after the company rather than hashed; the exact spelling is pinned
    # against the frontend's own output in the unit tests.
    assert organization.name == organization_name_from_work_domain(domain)
    # So the next colleague from this domain joins rather than fragments.
    assert organization.join_policy == OrganizationJoinPolicy.EMAIL_DOMAIN
    assert organization.email_domain == domain


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
    await uow.commit()

    assert second_workspace.entry == "domain_join"
    assert second_workspace.organization_id == first_workspace.organization_id
    assert second_workspace.pod_id is not None
    assert second_workspace.pod_id != first_workspace.pod_id
    assert second_workspace.assistant_id is not None


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


async def test_existing_membership_gets_a_personal_pod(signup_user, db_session):
    """Idempotent, so a retried onboarding leaves no second empty workspace."""
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

    owner = await signup_user(email=f"owner-{uuid4().hex[:8]}@gmail.com")
    owned = await ensure_first_workspace(
        uow,
        organization_service=_organization_service(uow),
        user_id=UUID(owner["id"]),
        email=owner["email"],
        full_name="Grace Hopper",
    )
    invitation = OrganizationInvitationEntity(
        email=invitee_email,
        organization_id=owned.organization_id,
        role=OrganizationRole.ORG_MEMBER,
        pod_id=owned.pod_id,
        pod_role="POD_USER",
    )
    await OrganizationRepository(uow).add_invitation(invitation)
    await uow.commit()
    return owned, invitation


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
