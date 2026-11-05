"""An org's own WhatsApp number knows its members only by a code they answered.

Its Meta app secret is the org's, so whoever holds it can sign a delivery from
any number. The sender's number therefore proves nothing there; a member
verifies once through that number -- the same private email-code exchange a
company's Slack or Teams installation runs -- and is then answered as
themselves. A forged ``from`` carrying a member's verified number, with no such
binding behind it, is not them.

Real auth, persistence and ingress, with the fake WhatsApp Cloud API.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.composition import build_surface_ingress
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.services.chat_onboarding import (
    ChatOnboardingCoordinator,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent,
    _create_surface,
    _ensure_connector_account,
    _set_user_mobile_number,
    _whatsapp_payload,
)
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.services.email_challenges import EmailChallengeService
from app.modules.identity.tests.e2e.test_email_challenges_e2e import allow_test_delivery

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_PHONE_NUMBER_ID = "2223334440"
_WABA = "waba-own-number"


class _OwnNumber:
    """The pod's own WhatsApp number, and a way to write to it."""

    def __init__(self, surface_id: UUID, coordinator, codes: list[str]) -> None:
        self.surface_id = surface_id
        self.coordinator = coordinator
        self.codes = codes

    def delivery(self, sender: str, text: str) -> SurfaceDirectWebhookIngress:
        return SurfaceDirectWebhookIngress(
            surface_id=self.surface_id,
            payload=_whatsapp_payload(
                text=text,
                message_id=f"wamid-{uuid4().hex}",
                phone_number_id=_PHONE_NUMBER_ID,
                waba_id=_WABA,
                sender_phone=sender,
            ),
        )

    async def onboard(self, sender: str, text: str) -> bool:
        return (await self.coordinator.handle(self.delivery(sender, text))).handled

    async def route(self, db_session: AsyncSession, sender: str):
        uow = SqlAlchemyUnitOfWork(db_session)
        context = await build_surface_ingress(uow).prepare_ingress(
            self.delivery(sender, "what is on my list today?")
        )
        await uow.commit()
        return context


async def _own_number(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_whatsapp
) -> _OwnNumber:
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="whatsapp",
        credentials={
            "access_token": "own-number-token",
            "phone_number_id": _PHONE_NUMBER_ID,
            "waba_id": _WABA,
            "app_secret": "the-orgs-own-app-secret",
            "verify_token": "own-number-verify",
            "api_base_url": f"{fake_whatsapp.api_base}/v21.0",
        },
    )
    agent = await _create_agent(authenticated_client, test_pod["id"])
    surface = await _create_surface(
        authenticated_client,
        test_pod["id"],
        config={
            "type": "WHATSAPP",
            "account_id": str(account.id),
            "credential_mode": "CUSTOM",
        },
        agent_name=agent["name"],
    )
    codes: list[str] = []

    async def capture(*, email: str, code: str) -> bool:
        codes.append(code)
        return True

    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    coordinator = ChatOnboardingCoordinator(
        SessionUnitOfWorkFactory(sessions),
        challenges=EmailChallengeService(
            sessions, send_email=capture, enforce_send_limits=allow_test_delivery
        ),
        email_deliverable=lambda: True,
    )
    return _OwnNumber(UUID(surface["id"]), coordinator, codes)


def _number() -> str:
    return "15551" + str(int(uuid4().hex[:7], 16)).zfill(9)


async def test_a_member_who_verifies_through_the_own_number_is_answered_as_themselves(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_whatsapp
):
    own = await _own_number(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_whatsapp
    )
    sender = _number()

    assert await own.onboard(sender, "hello")
    assert await own.onboard(sender, fixed_test_user["email"])
    assert len(own.codes) == 1
    assert await own.onboard(sender, own.codes[0])

    db_session.expire_all()
    binding = await db_session.scalar(
        select(VerifiedSurfaceIdentity).where(
            VerifiedSurfaceIdentity.external_user_id == sender,
            VerifiedSurfaceIdentity.user_id == UUID(fixed_test_user["id"]),
        )
    )
    # Bound on the mailbox code alone: a number this app delivered is its
    # secret holder's word, so none is recorded -- on the binding, or the user.
    assert binding is not None and binding.verified_phone is None
    user = await db_session.get(User, UUID(fixed_test_user["id"]))
    assert user is not None and user.mobile_number != f"+{sender}"
    assert (
        await db_session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding.binding_key
            )
        )
        is None
    )

    # Known now: the next message is ordinary, and it is theirs.
    assert not await own.onboard(sender, "what is on my list today?")
    context = await own.route(db_session, sender)
    assert isinstance(context, SurfaceChatContext), context
    assert str(context.user_id) == fixed_test_user["id"]


async def test_a_forged_from_with_a_members_verified_number_is_not_them(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_whatsapp
):
    own = await _own_number(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_whatsapp
    )
    members_number = _number()
    await _set_user_mobile_number(
        db_session, user_id=fixed_test_user["id"], mobile_number=members_number
    )

    context = await own.route(db_session, members_number)

    assert not (
        isinstance(context, SurfaceChatContext)
        and str(context.user_id) == fixed_test_user["id"]
        and not context.audience.is_contact
    ), context
