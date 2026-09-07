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


async def test_somebody_who_already_belongs_somewhere_gets_nothing_new(
    signup_user, db_session
):
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
    assert workspace.pod_id is None
