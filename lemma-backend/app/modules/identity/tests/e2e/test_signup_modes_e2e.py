"""The signup modes and the first-account rule, against real Postgres and Core.

The first-account rule gets a database of its own: "the first signup" only
means anything on a deployment with no accounts, and the shared per-worker
database has as many as every other test left behind.

The signup modes are driven through the real sign-up routes, because the gate
is only worth anything if every door that creates a user calls it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from supertokens_python.recipe.thirdparty.interfaces import (
    RecipeInterface,
    SignInUpNotAllowed,
)

from app.core.config import settings
from app.core.infrastructure.db.base import Base
from app.modules.identity.api.controllers import email_login_controller
from app.modules.identity.config import IdentitySettings, identity_settings
from app.modules.identity.domain.errors import SignupNotAllowedError
from app.modules.identity.domain.organization_entities import (
    OrganizationInvitationStatus,
    OrganizationRole,
)
from app.modules.identity.infrastructure.models import (
    OrganizationInvitation,
    User,
)
from app.modules.identity.infrastructure.signup_store import SqlSignupStore
from app.modules.identity.infrastructure.supertokens_auth.override_thirdparty import (
    override_thirdparty_functions,
)
from app.modules.identity.services.email_challenges import EmailChallengeService
from app.modules.identity.services.signup_gate import Admission, SignupGate
from app.modules.identity.tests.e2e.test_email_challenges_e2e import (
    Mailbox,
    allow_test_delivery,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# A deployment with nobody on it yet
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def empty_deployment(
    test_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A fresh database holding only the users table."""
    name = f"signup_{uuid4().hex[:12]}"
    server = make_url(test_database_url)
    admin = await asyncpg.connect(
        user=server.username,
        password=server.password,
        host=server.host,
        port=server.port,
        database="postgres",
    )
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    engine = create_async_engine(server.set(database=name))
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all, tables=[User.__table__])
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(
            user=server.username,
            password=server.password,
            host=server.host,
            port=server.port,
            database="postgres",
        )
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()


async def test_the_first_account_gets_in_through_a_closed_door_and_nobody_after(
    empty_deployment: async_sessionmaker[AsyncSession],
) -> None:
    # Closed, so the refusal below needs no invitation lookup -- this database
    # holds only the users table -- and so the first account is seen getting
    # in through a mode that admits nobody else.
    gate = SignupGate(
        settings=IdentitySettings(deployment_kind="desktop", signup_mode="closed"),
        store=SqlSignupStore(empty_deployment),
    )

    assert await gate.admit("me@example.com") is Admission.FIRST_ACCOUNT
    async with empty_deployment() as session, session.begin():
        session.add(User(id=uuid4(), email="me@example.com"))
    with pytest.raises(SignupNotAllowedError):
        await gate.admit("stranger@example.com")


# ---------------------------------------------------------------------------
# The signup modes, through the doors that create accounts
# ---------------------------------------------------------------------------


async def _invite(db_session: AsyncSession, organization_id: str, email: str) -> UUID:
    invitation = OrganizationInvitation(
        email=email,
        organization_id=UUID(organization_id),
        role=OrganizationRole.ORG_MEMBER,
        status=OrganizationInvitationStatus.PENDING,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db_session.add(invitation)
    await db_session.commit()
    return invitation.id


async def _sign_up(
    async_client: AsyncClient, email: str, *, invitation: UUID | None = None
) -> dict[str, object]:
    response = await async_client.post(
        "/st/auth/signup",
        json={
            "formFields": [
                {"id": "email", "value": email},
                {"id": "password", "value": "TestPassword@123"},
            ]
        },
        headers={"x-lemma-invitation": str(invitation)} if invitation else {},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_invite_only_refuses_a_stranger_and_admits_an_invited_address(
    async_client: AsyncClient,
    db_session: AsyncSession,
    fixed_test_org: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The in-process settings object, which the gate reads; the environment
    # would reach only the worker subprocess, and too late.
    monkeypatch.setattr(identity_settings, "signup_mode", "invite_only")
    invited = f"invited-{uuid4().hex[:10]}@example.com"
    await _invite(db_session, fixed_test_org["id"], invited)

    refused = await _sign_up(async_client, f"stranger-{uuid4().hex[:10]}@example.com")
    assert refused == {
        "status": "SIGN_UP_NOT_ALLOWED",
        "reason": SignupNotAllowedError.INVITE_ONLY_MESSAGE,
    }

    admitted = await _sign_up(async_client, invited)
    assert admitted["status"] == "OK", admitted

    monkeypatch.setattr(identity_settings, "signup_mode", "closed")
    also_invited = f"invited-{uuid4().hex[:10]}@example.com"
    await _invite(db_session, fixed_test_org["id"], also_invited)
    closed = await _sign_up(async_client, also_invited)
    assert closed == {
        "status": "SIGN_UP_NOT_ALLOWED",
        "reason": SignupNotAllowedError.CLOSED_MESSAGE,
    }


async def test_without_email_verification_an_invitation_must_be_presented(
    async_client: AsyncClient,
    db_session: AsyncSession,
    fixed_test_org: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shared Desktop installation: invite-only, and no address is ever proven.

    Knowing an invitee's address used to be enough to take their invitation,
    because the gate matched by address and nothing checked the address was
    the signer's. The id from the invitation link is what they alone hold.
    """
    monkeypatch.setattr(identity_settings, "signup_mode", "invite_only")
    monkeypatch.setattr(settings, "auth_email_verification_required", False)
    invited = f"invited-{uuid4().hex[:10]}@example.com"
    invitation = await _invite(db_session, fixed_test_org["id"], invited)

    by_address = await _sign_up(async_client, invited)
    assert by_address == {
        "status": "SIGN_UP_NOT_ALLOWED",
        "reason": SignupNotAllowedError.INVITE_ONLY_MESSAGE,
    }
    guessed = await _sign_up(async_client, invited, invitation=uuid4())
    assert guessed["status"] == "SIGN_UP_NOT_ALLOWED", guessed

    admitted = await _sign_up(async_client, invited, invitation=invitation)
    assert admitted["status"] == "OK", admitted


async def test_oauth_sign_up_obeys_the_mode_and_sign_in_does_not(
    db_session: AsyncSession,
    fixed_test_org: dict[str, str],
    fixed_test_user: dict[str, str],
) -> None:
    """An OAuth provider is another door to the same room.

    The provider's half is stood in front of the override -- nothing here can
    complete a real Google login -- and it answers with a refusal of its own so
    that reaching it is observable without creating anybody.
    """
    reached: list[str] = []

    async def provider_sign_in_up(
        third_party_id: str, third_party_user_id: str, email: str, *_args: object
    ) -> SignInUpNotAllowed:
        reached.append(email)
        return SignInUpNotAllowed("reached the provider")

    assert db_session.bind is not None
    gate = SignupGate(
        settings=IdentitySettings(signup_mode="invite_only"),
        store=SqlSignupStore(
            async_sessionmaker(db_session.bind, expire_on_commit=False)
        ),
    )
    recipe = override_thirdparty_functions(
        cast(RecipeInterface, SimpleNamespace(sign_in_up=provider_sign_in_up)),
        admit_signup=gate.admit,
    )
    invited = f"oauth-invited-{uuid4().hex[:10]}@example.com"
    await _invite(db_session, fixed_test_org["id"], invited)

    async def attempt(email: str) -> SignInUpNotAllowed:
        result = await recipe.sign_in_up(
            "google",
            uuid4().hex,
            email,
            True,
            {},
            SimpleNamespace(),
            None,
            None,
            "public",
            {},
        )
        assert isinstance(result, SignInUpNotAllowed)
        return result

    stranger = f"oauth-stranger-{uuid4().hex[:10]}@example.com"
    assert (await attempt(stranger)).reason == SignupNotAllowedError.INVITE_ONLY_MESSAGE
    assert (await attempt(invited)).reason == "reached the provider"
    # An existing member signing in is not a signup, whatever the mode says.
    # (Their account is email/password, so the override answers that conflict
    # itself -- the point is that it is not the invite-only refusal.)
    existing = await attempt(fixed_test_user["email"])
    assert existing.reason != SignupNotAllowedError.INVITE_ONLY_MESSAGE
    assert reached == [invited]


async def test_an_email_code_cannot_create_an_account_invite_only_refuses(
    async_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    test_app: FastAPI,
    fixed_test_user: dict[str, str],
) -> None:
    """A verified code proves a mailbox; it is not an invitation.

    `fixed_test_user` is here for its row: on a database with no accounts at
    all, the first one is admitted whatever the mode.
    """
    assert db_session.bind is not None
    mailbox = Mailbox()
    service = EmailChallengeService(
        async_sessionmaker(db_session.bind, expire_on_commit=False),
        send_email=mailbox.send,
        enforce_send_limits=allow_test_delivery,
    )
    monkeypatch.setitem(
        test_app.dependency_overrides,
        email_login_controller.get_email_login_challenges,
        lambda: service,
    )
    monkeypatch.setattr(identity_settings, "signup_mode", "invite_only")
    headers = {"origin": settings.auth_frontend_url.rstrip("/")}
    email = f"code-stranger-{uuid4().hex}@gmail.com"

    initialized = await async_client.post(
        "/auth/email-code/browser", json={}, headers=headers
    )
    assert initialized.status_code == 200, initialized.text
    nonce = initialized.json()["nonce"]
    started = await async_client.post(
        "/auth/email-code/start", json={"email": email, "nonce": nonce}, headers=headers
    )
    assert started.status_code == 200, started.text
    verified = await async_client.post(
        "/auth/email-code/verify",
        json={
            "challenge_id": started.json()["challenge_id"],
            "nonce": nonce,
            "code": mailbox.code,
        },
        headers=headers,
    )

    assert verified.status_code == 400, verified.text
    assert SignupNotAllowedError.INVITE_ONLY_MESSAGE in verified.text
    assert await db_session.scalar(select(User.id).where(User.email == email)) is None
