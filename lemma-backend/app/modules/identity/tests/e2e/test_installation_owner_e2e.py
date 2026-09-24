"""The installation owner and the signup modes, against real Postgres and Core.

The owner slot's guarantee is a database one -- a singleton key and an
`ON CONFLICT DO NOTHING` -- so the race is run against Postgres rather than
reasoned about. It gets a database of its own: "the first signup" only means
anything on an installation with no accounts, and the shared per-worker
database has as many as every other test left behind.

The signup modes are driven through the real sign-up routes, because the gate
is only worth anything if every door that creates a user calls it.
"""

from __future__ import annotations

import asyncio
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
from app.modules.identity.infrastructure.installation_owner_store import (
    SqlInstallationOwnerStore,
    bind_installation_owner,
)
from app.modules.identity.infrastructure.models import (
    InstallationOwner,
    OrganizationInvitation,
    User,
)
from app.modules.identity.infrastructure.supertokens_auth.override_thirdparty import (
    override_thirdparty_functions,
)
from app.modules.identity.services.email_challenges import EmailChallengeService
from app.modules.identity.services.installation import Admission, SignupGate
from app.modules.identity.tests.e2e.test_email_challenges_e2e import (
    Mailbox,
    allow_test_delivery,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_TTL = timedelta(minutes=10)


# ---------------------------------------------------------------------------
# An installation with nobody on it yet
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def empty_installation(
    test_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A fresh database holding only the tables the owner slot touches."""
    name = f"installation_{uuid4().hex[:12]}"
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
            await connection.run_sync(
                Base.metadata.create_all,
                tables=[User.__table__, InstallationOwner.__table__],
            )
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


async def _create_user(sessions: async_sessionmaker[AsyncSession], email: str) -> UUID:
    """A user row and its owner binding, in one transaction as production does."""
    user_id = uuid4()
    async with sessions() as session, session.begin():
        session.add(User(id=user_id, email=email))
        await session.flush()
        await bind_installation_owner(session, user_id=user_id, email=email)
    return user_id


async def test_two_concurrent_first_signups_make_exactly_one_owner(
    empty_installation: async_sessionmaker[AsyncSession],
) -> None:
    store = SqlInstallationOwnerStore(empty_installation)
    now = datetime.now(timezone.utc)
    emails = [f"first-{index}@example.com" for index in range(8)]

    admitted = await asyncio.gather(
        *(
            store.reserve_first_signup(email, now=now, reservation_ttl=_TTL)
            for email in emails
        )
    )

    assert admitted.count(True) == 1, admitted
    winner = emails[admitted.index(True)]
    winner_id = await _create_user(empty_installation, winner)
    # A loser that got an account some other way (an open-mode signup) is a
    # member, and binding its account must not move the owner.
    loser_id = await _create_user(empty_installation, emails[admitted.index(False)])

    desktop = SignupGate(
        settings=IdentitySettings(deployment_kind="desktop"), store=store
    )
    assert await desktop.is_installation_owner(winner_id) is True
    assert await desktop.is_installation_owner(loser_id) is False
    async with empty_installation() as session:
        rows = (await session.scalars(select(InstallationOwner))).all()
    assert [(row.email, row.user_id) for row in rows] == [(winner, winner_id)]
    # And once it is bound, nobody is ever "first" again.
    assert not await store.reserve_first_signup(
        "late@example.com", now=datetime.now(timezone.utc), reservation_ttl=_TTL
    )


async def test_the_same_address_retrying_keeps_its_reservation(
    empty_installation: async_sessionmaker[AsyncSession],
) -> None:
    """A first signup refused by the password policy and resubmitted is still first."""
    store = SqlInstallationOwnerStore(empty_installation)
    now = datetime.now(timezone.utc)

    assert await store.reserve_first_signup(
        "me@example.com", now=now, reservation_ttl=_TTL
    )
    assert not await store.reserve_first_signup(
        "someone@example.com", now=now, reservation_ttl=_TTL
    )
    assert await store.reserve_first_signup(
        "me@example.com", now=now, reservation_ttl=_TTL
    )


async def test_an_abandoned_reservation_is_taken_over_once_it_is_stale(
    empty_installation: async_sessionmaker[AsyncSession],
) -> None:
    store = SqlInstallationOwnerStore(empty_installation)
    then = datetime.now(timezone.utc) - _TTL - timedelta(seconds=1)

    assert await store.reserve_first_signup(
        "gone@example.com", now=then, reservation_ttl=_TTL
    )
    assert await store.reserve_first_signup(
        "me@example.com", now=datetime.now(timezone.utc), reservation_ttl=_TTL
    )
    me = await _create_user(empty_installation, "me@example.com")

    assert (
        await store.owner_user_id(now=datetime.now(timezone.utc), reservation_ttl=_TTL)
        == me
    )


async def test_an_abandoned_reservation_does_not_leave_the_installation_ownerless(
    empty_installation: async_sessionmaker[AsyncSession],
) -> None:
    """Somebody got in (open mode) while the first signup was abandoned."""
    store = SqlInstallationOwnerStore(empty_installation)
    then = datetime.now(timezone.utc) - _TTL - timedelta(seconds=1)
    assert await store.reserve_first_signup(
        "gone@example.com", now=then, reservation_ttl=_TTL
    )
    member = await _create_user(empty_installation, "member@example.com")

    assert (
        await store.owner_user_id(now=datetime.now(timezone.utc), reservation_ttl=_TTL)
        == member
    )


async def test_an_installation_upgraded_with_accounts_makes_its_oldest_the_owner(
    empty_installation: async_sessionmaker[AsyncSession],
) -> None:
    """No backfill runs in the migration; the first question asked settles it."""
    oldest = uuid4()
    async with empty_installation() as session, session.begin():
        session.add(
            User(
                id=oldest,
                email="oldest@example.com",
                created_at=datetime.now(timezone.utc) - timedelta(days=30),
            )
        )
        session.add(User(id=uuid4(), email="newer@example.com"))
    store = SqlInstallationOwnerStore(empty_installation)

    assert not await store.reserve_first_signup(
        "stranger@example.com", now=datetime.now(timezone.utc), reservation_ttl=_TTL
    )
    assert (
        await store.owner_user_id(now=datetime.now(timezone.utc), reservation_ttl=_TTL)
        == oldest
    )


# ---------------------------------------------------------------------------
# The signup modes, through the doors that create accounts
# ---------------------------------------------------------------------------


async def _invite(db_session: AsyncSession, organization_id: str, email: str) -> None:
    db_session.add(
        OrganizationInvitation(
            email=email,
            organization_id=UUID(organization_id),
            role=OrganizationRole.ORG_MEMBER,
            status=OrganizationInvitationStatus.PENDING,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
    )
    await db_session.commit()


async def _sign_up(async_client: AsyncClient, email: str) -> dict[str, object]:
    response = await async_client.post(
        "/st/auth/signup",
        json={
            "formFields": [
                {"id": "email", "value": email},
                {"id": "password", "value": "TestPassword@123"},
            ]
        },
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
        store=SqlInstallationOwnerStore(
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
) -> None:
    """A verified code proves a mailbox; it is not an invitation."""
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


async def test_the_installation_endpoint_reports_the_callers_standing(
    authenticated_client: AsyncClient,
) -> None:
    response = await authenticated_client.get("/users/me/installation")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "deployment": "server",
        "is_owner": False,
        "signup_mode": "open",
    }


async def test_the_gate_admits_the_first_desktop_account_as_owner(
    empty_installation: async_sessionmaker[AsyncSession],
) -> None:
    # Closed, so the refusal below needs no invitation lookup -- this database
    # holds only the owner's tables -- and so the owner is seen getting in
    # through a mode that admits nobody else.
    gate = SignupGate(
        settings=IdentitySettings(deployment_kind="desktop", signup_mode="closed"),
        store=SqlInstallationOwnerStore(empty_installation),
    )

    assert await gate.admit("me@example.com") is Admission.OWNER
    me = await _create_user(empty_installation, "me@example.com")
    with pytest.raises(SignupNotAllowedError):
        await gate.admit("stranger@example.com")
    assert (await gate.view_for(me)).is_owner is True
