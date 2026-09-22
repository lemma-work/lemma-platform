"""A plan's member cap, held against a real database, on every way in.

The cap counts people and the invitations waiting for them: an invitation is a
promise of a seat. So an organization at its cap can neither invite nor be
joined, and an invitation already sent can still be accepted.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.events.message_bus import get_message_bus
from app.core.ports.plan_limits import PodAllowance
from app.modules.identity.infrastructure.adapters.pod_membership_adapter import (
    SqlAlchemyPodMembershipAdapter,
)
from app.modules.identity.infrastructure.organization_repositories import (
    OrganizationRepository,
)
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.identity.services.first_workspace import ensure_first_workspace
from app.modules.identity.services.organization_service import OrganizationService
from app.modules.test_support.e2e_authz import auth_headers
from app.modules.test_support.e2e_authz import signup_user as sign_up
from app.modules.test_support.plan_limits import SetPlan, plan

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

__all__ = ["plan"]


async def _organization(
    client: AsyncClient, owner: dict, *, join_policy: str = "INVITE_ONLY"
) -> str:
    response = await client.post(
        "/organizations",
        json={"name": f"Capped {uuid4().hex[:8]}", "join_policy": join_policy},
        headers=auth_headers(owner),
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _invite(client: AsyncClient, owner: dict, organization_id: str):
    return await client.post(
        f"/organizations/{organization_id}/invitations",
        json={
            "email": f"test+invitee-{uuid4().hex[:8]}@example.com",
            "role": "ORG_MEMBER",
        },
        headers=auth_headers(owner),
    )


async def test_invitations_stop_at_the_cap_counting_those_already_sent(
    async_client: AsyncClient, plan: SetPlan
):
    owner = await sign_up(async_client, "member-cap-owner")
    organization_id = await _organization(async_client, owner)
    plan.members = 3
    for _ in range(2):
        sent = await _invite(async_client, owner, organization_id)
        assert sent.status_code == status.HTTP_201_CREATED, sent.text

    refused = await _invite(async_client, owner, organization_id)

    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text
    assert refused.json()["code"] == "MEMBER_LIMIT_REACHED"
    # One owner and two invitations: the invitations hold their seats.
    assert refused.json()["details"] == {"limit": 3, "used": 3}


async def test_an_invitation_sent_within_the_cap_can_be_accepted_at_it(
    async_client: AsyncClient, plan: SetPlan
):
    owner = await sign_up(async_client, "member-cap-owner")
    organization_id = await _organization(async_client, owner)
    invitee = await sign_up(async_client, "member-cap-invitee")
    plan.members = 2
    sent = await async_client.post(
        f"/organizations/{organization_id}/invitations",
        json={"email": invitee["email"], "role": "ORG_MEMBER"},
        headers=auth_headers(owner),
    )
    assert sent.status_code == status.HTTP_201_CREATED, sent.text

    accepted = await async_client.post(
        f"/organizations/invitations/{sent.json()['id']}/accept",
        headers=auth_headers(invitee),
    )

    assert accepted.status_code == status.HTTP_200_OK, accepted.text


async def test_revoking_an_invitation_frees_its_seat(
    async_client: AsyncClient, plan: SetPlan
):
    owner = await sign_up(async_client, "member-cap-owner")
    organization_id = await _organization(async_client, owner)
    plan.members = 2
    sent = await _invite(async_client, owner, organization_id)
    assert (await _invite(async_client, owner, organization_id)).status_code == 403

    revoked = await async_client.delete(
        f"/organizations/invitations/{sent.json()['id']}",
        headers=auth_headers(owner),
    )
    assert revoked.status_code == status.HTTP_204_NO_CONTENT, revoked.text

    again = await _invite(async_client, owner, organization_id)
    assert again.status_code == status.HTTP_201_CREATED, again.text


async def test_nobody_can_join_a_full_organization_on_their_own(
    async_client: AsyncClient, plan: SetPlan
):
    owner = await sign_up(async_client, "member-cap-owner")
    organization_id = await _organization(async_client, owner, join_policy="PUBLIC")
    joiner = await sign_up(async_client, "member-cap-joiner")
    plan.members = 1

    refused = await async_client.post(
        f"/organizations/{organization_id}/join", headers=auth_headers(joiner)
    )

    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text
    assert refused.json()["code"] == "MEMBER_LIMIT_REACHED"

    plan.members = 2
    joined = await async_client.post(
        f"/organizations/{organization_id}/join", headers=auth_headers(joiner)
    )
    assert joined.status_code == status.HTTP_200_OK, joined.text


async def test_with_no_plan_declared_nobody_is_turned_away(async_client: AsyncClient):
    owner = await sign_up(async_client, "member-cap-owner")
    organization_id = await _organization(async_client, owner)

    for _ in range(4):
        sent = await _invite(async_client, owner, organization_id)
        assert sent.status_code == status.HTTP_201_CREATED, sent.text


def _first_workspace_service(uow: SqlAlchemyUnitOfWork) -> OrganizationService:
    message_bus = get_message_bus()
    return OrganizationService(
        organization_repository=OrganizationRepository(uow, message_bus=message_bus),
        user_repository=UserRepository(uow, message_bus=message_bus),
        invitation_accept_base_url="https://app.example.test",
        pod_membership_port=SqlAlchemyPodMembershipAdapter(uow),
    )


async def test_a_colleague_arriving_at_a_full_company_starts_their_own(
    signup_user, db_session, plan: SetPlan
):
    """Signing up never dead-ends on a full organization. The colleague lands in
    one of their own, which claims nothing: the company still holds the domain,
    so the next colleague still finds the company once it has room."""
    domain = f"full-{uuid4().hex[:8]}.com"
    uow = SqlAlchemyUnitOfWork(db_session)
    first = await signup_user(email=f"ada@{domain}")
    company = await ensure_first_workspace(
        uow,
        organization_service=_first_workspace_service(uow),
        user_id=UUID(first["id"]),
        email=first["email"],
        full_name="Ada Lovelace",
    )
    await uow.commit()
    plan.members = 1

    second = await signup_user(email=f"grace@{domain}")
    arrived = await ensure_first_workspace(
        uow,
        organization_service=_first_workspace_service(uow),
        user_id=UUID(second["id"]),
        email=second["email"],
        full_name="Grace Hopper",
    )
    await uow.commit()

    assert arrived.entry == "new_org"
    assert arrived.organization_id != company.organization_id
    own = await OrganizationRepository(uow).get(arrived.organization_id)
    assert own is not None
    assert own.email_domain is None
    still_the_company = await OrganizationRepository(uow).get(company.organization_id)
    assert still_the_company is not None
    assert still_the_company.email_domain == domain


async def test_joining_with_no_room_for_a_pod_still_joins(
    signup_user, db_session, plan: SetPlan
):
    """The pod made for someone on arrival counts like any other. A plan with
    no room for it leaves them without one; it does not stop them arriving."""
    signed_up = await signup_user(email=f"ada-{uuid4().hex[:8]}@gmail.com")
    plan.pods = PodAllowance(limit=0)
    uow = SqlAlchemyUnitOfWork(db_session)

    workspace = await ensure_first_workspace(
        uow,
        organization_service=_first_workspace_service(uow),
        user_id=UUID(signed_up["id"]),
        email=signed_up["email"],
        full_name="Ada Lovelace",
    )
    await uow.commit()

    assert workspace.organization_id is not None
    assert workspace.pod_id is None
    assert workspace.pod_created is False
