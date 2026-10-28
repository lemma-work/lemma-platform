"""A member proves which Telegram account is theirs -- end to end.

The failure this answers: a pod member with no verified phone could never be
recognised by the pod's Telegram bot, and in a group ran as a stranger, with
Public reads only. Now they mint a code in their Lemma session, open it in
Telegram, and confirm the account it was opened from. Each test pins one rule:

* confirmed, the account is the member's on every bot -- a group line from it
  is theirs -- and a phone change does not undo it;
* opened, but not confirmed, nothing is bound: a forwarded link gives whoever
  opened it nothing;
* made for somebody the pod took for a stranger, it works only from that
  account; from another it is spent and the member told;
* an account already somebody else's is not taken by proving it again.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurfaceExternalUser
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.infrastructure.repositories.verified_surface_identity_repository import (  # noqa: E501
    VerifiedSurfaceIdentityRepository,
)
from app.modules.agent_surfaces.services.onboarding_transport import binding_key_of
from app.modules.agent_surfaces.services.telegram_identity_link import (
    OPENED_REPLY,
    OTHER_ACCOUNT_REPLY,
)
from app.modules.agent_surfaces.services.telegram_identity_link_claim import (
    claim_telegram_identity_link,
)
from app.modules.agent_surfaces.services.verified_surface_identity import (
    resolve_shared_verified_identity,
)
from app.modules.agent_surfaces.tests.e2e.helpers import _create_surface
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    wait_for_messages,
)
from app.modules.agent_surfaces.tests.e2e.test_space_groups_e2e import (
    _wire_native_telegram,
)
from app.modules.contacts.contracts import IdentityKind, IdentityStrength, open_contact
from app.modules.test_support.e2e_authz import signup_user

pytestmark = pytest.mark.e2e

MEMBERS_ACCOUNT = 7_000_101
SOMEBODY_ELSE = 7_000_202


async def _bot(client: AsyncClient, pod_id: str, monkeypatch, fake) -> dict:
    _wire_native_telegram(monkeypatch, fake)
    return await _create_surface(client, pod_id, config={"type": "TELEGRAM"})


async def _mint(
    client: AsyncClient, pod_id: str, surface_id: str, contact_id: str | None = None
) -> str:
    response = await client.post(
        f"/pods/{pod_id}/telegram-identity-links",
        json={"surface_id": surface_id, "contact_id": contact_id},
    )
    assert response.status_code == 200, response.text
    url = response.json()["url"]
    assert url.startswith("https://t.me/lemmabot?start=lk_")
    return response.json()["code"]


async def _opened_from(account: int, code: str, *, name: str = "Anu") -> bool:
    """``/start lk_<code>`` in a private chat with the bot, from ``account``."""
    return await claim_telegram_identity_link(
        SessionUnitOfWorkFactory(async_session_maker),
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload={
                "update_id": account,
                "message": {
                    "message_id": 1,
                    "from": {
                        "id": account,
                        "is_bot": False,
                        "first_name": name,
                        "username": name.lower() + "_tg",
                    },
                    "chat": {"id": account, "type": "private", "first_name": name},
                    "date": 1700000100,
                    "text": f"/start lk_{code}",
                    "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
                },
            },
            headers={},
        ),
        adapters=SurfacePlatformAdapterRegistry(),
    )


async def _status(client: AsyncClient, pod_id: str, code: str) -> dict:
    response = await client.get(f"/pods/{pod_id}/telegram-identity-links/{code}")
    assert response.status_code == 200, response.text
    return response.json()


async def _binding(db_session: AsyncSession, account: int):
    return await db_session.scalar(
        select(VerifiedSurfaceIdentity).where(
            VerifiedSurfaceIdentity.platform == "TELEGRAM",
            VerifiedSurfaceIdentity.external_user_id == str(account),
        )
    )


def _dm_from(account: int) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.TELEGRAM,
        conversation_type=ConversationType.EXTERNAL_GROUP,
        external_thread_id="-100555",
        external_channel_id="-100555",
        sender_external_user_id=str(account),
        message_text="hi",
    )


async def test_a_confirmed_account_is_the_members_everywhere_and_outlives_a_phone(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    bot = await _bot(authenticated_client, pod_id, monkeypatch, fake_telegram)
    code = await _mint(authenticated_client, pod_id, bot["id"])
    assert (await _status(authenticated_client, pod_id, code))["state"] == "WAITING"

    assert await _opened_from(MEMBERS_ACCOUNT, code) is True

    # The bot answers whoever opened it, and names nobody.
    sent = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    assert sent[-1]["chat_id"] == str(MEMBERS_ACCOUNT)
    assert sent[-1]["text"] == OPENED_REPLY
    status = await _status(authenticated_client, pod_id, code)
    assert status["state"] == "OPENED"
    assert status["account"] == {"name": "Anu", "username": "anu_tg"}
    # Opened is not linked.
    assert await _binding(db_session, MEMBERS_ACCOUNT) is None

    confirmed = await authenticated_client.post(
        f"/pods/{pod_id}/telegram-identity-links/{code}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text

    db_session.expire_all()
    binding = await _binding(db_session, MEMBERS_ACCOUNT)
    assert binding is not None and binding.user_id == UUID(fixed_test_user["id"])
    assert binding.session_proved_at is not None and binding.verified_phone is None
    cached = await db_session.scalar(
        select(AgentSurfaceExternalUser).where(
            AgentSurfaceExternalUser.platform == "TELEGRAM",
            AgentSurfaceExternalUser.external_user_id == str(MEMBERS_ACCOUNT),
        )
    )
    assert cached is not None and cached.resolved_user_id == UUID(fixed_test_user["id"])

    # A group line from the account is the member's, with no phone at all.
    uow = SqlAlchemyUnitOfWork(db_session)
    resolved = await resolve_shared_verified_identity(uow, _dm_from(MEMBERS_ACCOUNT))
    assert resolved is not None
    assert resolved.internal_user_id == UUID(fixed_test_user["id"])

    # A phone change revokes what stood on a number; this stands on the proof.
    await VerifiedSurfaceIdentityRepository(uow).revoke_phone_bound_except(
        UUID(fixed_test_user["id"]), "+15550009999"
    )
    await db_session.commit()
    db_session.expire_all()
    still = await _binding(db_session, MEMBERS_ACCOUNT)
    assert still is not None and still.revoked_at is None

    # Spent.
    assert (await _status(authenticated_client, pod_id, code))["state"] == "GONE"


async def test_an_opened_link_binds_nothing_until_the_member_confirms_it(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """A forwarded link: whoever opened it is shown, and declined."""
    pod_id = test_pod["id"]
    bot = await _bot(authenticated_client, pod_id, monkeypatch, fake_telegram)
    code = await _mint(authenticated_client, pod_id, bot["id"])

    await _opened_from(SOMEBODY_ELSE, code, name="Stranger")
    status = await _status(authenticated_client, pod_id, code)
    assert status["account"]["name"] == "Stranger"

    declined = await authenticated_client.post(
        f"/pods/{pod_id}/telegram-identity-links/{code}/decline"
    )
    assert declined.status_code == 204
    assert await _binding(db_session, SOMEBODY_ELSE) is None
    gone = await authenticated_client.post(
        f"/pods/{pod_id}/telegram-identity-links/{code}/confirm"
    )
    assert gone.status_code == 409


async def test_a_link_for_somebody_in_a_group_works_only_from_their_account(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fake_telegram,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    bot = await _bot(authenticated_client, pod_id, monkeypatch, fake_telegram)
    guest = await open_contact(
        SqlAlchemyUnitOfWork(db_session),
        pod_id=UUID(pod_id),
        kind=IdentityKind.TELEGRAM,
        value=str(MEMBERS_ACCOUNT),
        strength=IdentityStrength.CHANNEL,
        display_name="Anu",
    )
    await db_session.commit()
    code = await _mint(authenticated_client, pod_id, bot["id"], str(guest.id))

    await _opened_from(SOMEBODY_ELSE, code)

    sent = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    assert sent[-1]["text"] == OTHER_ACCOUNT_REPLY
    status = await _status(authenticated_client, pod_id, code)
    assert status["state"] == "OTHER_ACCOUNT"
    # Spent: the right account is too late now.
    await _opened_from(MEMBERS_ACCOUNT, code)
    assert (await _status(authenticated_client, pod_id, code))["state"] == (
        "OTHER_ACCOUNT"
    )
    assert await _binding(db_session, SOMEBODY_ELSE) is None
    assert await _binding(db_session, MEMBERS_ACCOUNT) is None


async def test_an_account_already_somebody_elses_is_not_taken(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fake_telegram,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    bot = await _bot(authenticated_client, pod_id, monkeypatch, fake_telegram)
    # Another person proved the account first.
    other = await signup_user(async_client, "first-prover")
    await VerifiedSurfaceIdentityRepository(
        SqlAlchemyUnitOfWork(db_session)
    ).prove_by_session(
        binding_key=binding_key_of(
            "TELEGRAM", None, installation_id=None, actor=str(SOMEBODY_ELSE)
        ),
        platform="TELEGRAM",
        external_user_id=str(SOMEBODY_ELSE),
        user_id=UUID(other["id"]),
        surface_id=UUID(bot["id"]),
    )
    await db_session.commit()

    code = await _mint(authenticated_client, pod_id, bot["id"])
    await _opened_from(SOMEBODY_ELSE, code)
    refused = await authenticated_client.post(
        f"/pods/{pod_id}/telegram-identity-links/{code}/confirm"
    )

    assert refused.status_code == 409
    db_session.expire_all()
    binding = await _binding(db_session, SOMEBODY_ELSE)
    assert binding is not None and binding.user_id == UUID(other["id"])
