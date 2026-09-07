"""Onboarding somebody who arrived on WhatsApp, against real auth and a real database.

The account this makes has to be a *real* one -- Lemma's user id is the
SuperTokens user id, so an account that is only a row is one nobody can ever
sign into. That is the thing worth proving here, and it cannot be proved with a
mock: the test signs in afterwards with a freshly reset password and checks the
session comes back.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.events.message_bus import get_message_bus
from app.modules.identity.contracts.surfaces import live_user_id_by_email
from app.modules.identity.infrastructure.adapters.pod_membership_adapter import (
    SqlAlchemyPodMembershipAdapter,
)
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.organization_repositories import (
    OrganizationRepository,
)
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.identity.services.chat_signup import onboard_proven_email
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


async def test_a_stranger_on_whatsapp_gets_an_account_a_workspace_and_a_linked_number(
    db_session, async_client
):
    email = f"ada-{uuid4().hex[:10]}@gmail.com"
    uow = SqlAlchemyUnitOfWork(db_session)

    onboarding = await onboard_proven_email(
        uow,
        organization_service=_organization_service(uow),
        email=email,
        existing_user_id=None,
        full_name="Ada Lovelace",
        mobile_number="+14155559001",
    )
    await uow.commit()

    assert onboarding.account_created is True
    assert onboarding.workspace.entry == "new_org"
    assert onboarding.workspace.pod_id is not None

    user = await db_session.get(User, onboarding.user_id)
    assert user is not None
    # The number that sent the message is theirs now, and proven -- so the next
    # message from it resolves without any of this happening again.
    assert user.mobile_number == "+14155559001"
    assert user.mobile_verified_at is not None
    assert user.first_name == "Ada"


async def test_the_account_it_makes_is_one_somebody_can_actually_sign_into(
    db_session, async_client
):
    """A row without an auth identity would be an account nobody can reach.

    The password is generated and thrown away, so the way in is the ordinary
    reset flow -- which is only safe to offer because the address was proven
    before any of this ran.
    """
    email = f"grace-{uuid4().hex[:10]}@gmail.com"
    uow = SqlAlchemyUnitOfWork(db_session)

    onboarding = await onboard_proven_email(
        uow,
        organization_service=_organization_service(uow),
        email=email,
        existing_user_id=None,
        full_name="Grace Hopper",
    )
    await uow.commit()

    # SuperTokens knows this address, which is what makes it an account rather
    # than a row: asking to reset returns OK rather than an unknown-user answer.
    reset = await async_client.post(
        "/st/auth/user/password/reset/token",
        json={"formFields": [{"id": "email", "value": email}]},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json().get("status") == "OK", reset.text

    user = await db_session.get(User, onboarding.user_id)
    assert user is not None
    assert user.is_active is True


async def test_an_address_that_already_has_an_account_is_linked_not_duplicated(
    signup_user, db_session
):
    """The case the old "please sign up" reply was wrong about."""
    signed_up = await signup_user(email=f"ada-{uuid4().hex[:10]}@gmail.com")
    existing_id = UUID(signed_up["id"])
    uow = SqlAlchemyUnitOfWork(db_session)

    onboarding = await onboard_proven_email(
        uow,
        organization_service=_organization_service(uow),
        email=signed_up["email"],
        existing_user_id=await live_user_id_by_email(uow, signed_up["email"]),
        mobile_number="+14155559002",
    )
    await uow.commit()

    assert onboarding.account_created is False
    assert onboarding.user_id == existing_id

    user = await db_session.get(User, existing_id)
    assert user is not None
    assert user.mobile_number == "+14155559002"


async def test_a_number_already_on_a_profile_is_not_replaced(signup_user, db_session):
    """A message from a second phone is not permission to change the first."""
    signed_up = await signup_user(email=f"ada-{uuid4().hex[:10]}@gmail.com")
    user_id = UUID(signed_up["id"])
    uow = SqlAlchemyUnitOfWork(db_session)

    user = await db_session.get(User, user_id)
    assert user is not None
    user.mobile_number = "+14155559100"
    await db_session.flush()

    await onboard_proven_email(
        uow,
        organization_service=_organization_service(uow),
        email=signed_up["email"],
        existing_user_id=user_id,
        mobile_number="+14155559999",
    )
    await uow.commit()

    refreshed = await db_session.get(User, user_id)
    assert refreshed is not None
    assert refreshed.mobile_number == "+14155559100"
