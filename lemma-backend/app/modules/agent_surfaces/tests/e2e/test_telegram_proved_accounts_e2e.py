"""A member sees the Telegram accounts they proved, and unlinks one -- end to end.

Proving an account from a Lemma session (``test_telegram_identity_link_e2e``)
makes it the member's on every bot, so the list and the unlink answer for the
member across every pod (``/surfaces/me/telegram-accounts``). Each test pins
one rule:

* a proved account is listed, and once unlinked is nobody's: neither the
  binding, the sender cache, nor the resolver says it is the member, and it can
  be proved again;
* nobody unlinks an account for somebody else, and a binding made some other
  way -- a shared phone number -- is neither listed nor unlinked here.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.infrastructure.models import AgentSurfaceExternalUser
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (  # noqa: E501
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.verified_surface_identity_repository import (  # noqa: E501
    VerifiedSurfaceIdentityRepository,
)
from app.modules.agent_surfaces.services.identity_resolution_service import (
    SurfaceIdentityResolutionService,
)
from app.modules.agent_surfaces.services.onboarding_transport import binding_key_of
from app.modules.agent_surfaces.tests.e2e.test_telegram_identity_link_e2e import (
    _binding,
    _bot,
    _dm_from,
    _mint,
    _opened_from,
)
from app.modules.test_support.e2e_authz import auth_headers, signup_user

pytestmark = pytest.mark.e2e

MEMBERS_ACCOUNT = 7_000_303
THEIR_ACCOUNT = 7_000_404
PHONE_ACCOUNT = 7_000_505
ACCOUNTS = "/surfaces/me/telegram-accounts"


async def _prove(client: AsyncClient, pod_id: str, bot_id: str, account: int) -> None:
    code = await _mint(client, pod_id, bot_id)
    assert await _opened_from(account, code, name="Dana") is True
    confirmed = await client.post(
        f"/pods/{pod_id}/telegram-identity-links/{code}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text


async def _listed(client: AsyncClient, **kwargs) -> list[dict]:
    response = await client.get(ACCOUNTS, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def test_an_unlinked_account_is_nobodys_and_can_be_proved_again(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    member = UUID(fixed_test_user["id"])
    bot = await _bot(authenticated_client, pod_id, monkeypatch, fake_telegram)
    await _prove(authenticated_client, pod_id, bot["id"], MEMBERS_ACCOUNT)

    db_session.expire_all()
    binding_id = (await _binding(db_session, MEMBERS_ACCOUNT)).id
    [listed] = await _listed(authenticated_client)
    assert listed["id"] == str(binding_id)
    assert listed["name"] == "Dana"
    assert listed["proved_at"] is not None

    unlinked = await authenticated_client.delete(f"{ACCOUNTS}/{binding_id}")
    assert unlinked.status_code == 204, unlinked.text
    assert await _listed(authenticated_client) == []

    db_session.expire_all()
    revoked = await _binding(db_session, MEMBERS_ACCOUNT)
    assert revoked.revoked_at is not None
    assert revoked.session_proved_at is None
    # A group's roster and the member's notifications read this cache directly.
    cached = await db_session.scalar(
        select(AgentSurfaceExternalUser).where(
            AgentSurfaceExternalUser.platform == "TELEGRAM",
            AgentSurfaceExternalUser.external_user_id == str(MEMBERS_ACCOUNT),
        )
    )
    assert cached is not None and cached.resolved_user_id is None
    uow = SqlAlchemyUnitOfWork(db_session)
    resolved = await SurfaceIdentityResolutionService(
        uow, ExternalSurfaceUserRepository(uow)
    ).resolve(event=_dm_from(MEMBERS_ACCOUNT))
    assert resolved.internal_user_id is None

    # Twice is not found: it is not theirs any more.
    again = await authenticated_client.delete(f"{ACCOUNTS}/{binding_id}")
    assert again.status_code == 404

    # Proved again, it is theirs again.
    await _prove(authenticated_client, pod_id, bot["id"], MEMBERS_ACCOUNT)
    db_session.expire_all()
    relinked = await _binding(db_session, MEMBERS_ACCOUNT)
    assert relinked.user_id == member and relinked.revoked_at is None
    assert [one["id"] for one in await _listed(authenticated_client)] == [
        str(relinked.id)
    ]


async def test_nobody_unlinks_an_account_for_somebody_else(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    bot = await _bot(authenticated_client, pod_id, monkeypatch, fake_telegram)
    other = await signup_user(async_client, "their-telegram")
    repository = VerifiedSurfaceIdentityRepository(SqlAlchemyUnitOfWork(db_session))
    theirs = await repository.prove_by_session(
        binding_key=binding_key_of(
            "TELEGRAM", None, installation_id=None, actor=str(THEIR_ACCOUNT)
        ),
        platform="TELEGRAM",
        external_user_id=str(THEIR_ACCOUNT),
        user_id=UUID(other["id"]),
        surface_id=UUID(bot["id"]),
    )
    # The member's own, but bound by a shared phone number, not from a session.
    by_phone = VerifiedSurfaceIdentity(
        binding_key=binding_key_of(
            "TELEGRAM", None, installation_id=None, actor=str(PHONE_ACCOUNT)
        ),
        platform="TELEGRAM",
        tenant_id="",
        external_user_id=str(PHONE_ACCOUNT),
        user_id=UUID(fixed_test_user["id"]),
        verified_phone="+15550001234",
    )
    db_session.add(by_phone)
    await db_session.flush()
    their_id, phone_id = theirs.id, by_phone.id
    await db_session.commit()

    assert await _listed(authenticated_client) == []
    for wanted in (their_id, phone_id, uuid4()):
        refused = await authenticated_client.delete(f"{ACCOUNTS}/{wanted}")
        assert refused.status_code == 404, refused.text

    db_session.expire_all()
    assert (await _binding(db_session, THEIR_ACCOUNT)).revoked_at is None
    assert (await _binding(db_session, PHONE_ACCOUNT)).revoked_at is None
    # Theirs is listed to them, and only to them.
    [listed] = await _listed(async_client, headers=auth_headers(other))
    assert listed["id"] == str(their_id)
