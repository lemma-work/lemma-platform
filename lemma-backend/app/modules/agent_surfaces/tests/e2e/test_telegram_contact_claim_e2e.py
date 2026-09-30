"""A self-shared Telegram contact finds the account whose profile has that number.

The Desktop path: the person who installed Lemma types their mobile number into
their profile (unverified -- Desktop has no way to verify it), messages the
shared bot and taps Share my contact. With
``SURFACE_ALLOW_UNVERIFIED_PHONE_MATCH`` on, that proven number matches the one
profile claiming it and the chat is linked without an email. Everything else
here is the edges of that rule: the switch, the proof, a verified owner, and a
server that cannot send the email a stranger would otherwise be asked for.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_job_queue import SharedStreaqJobQueue
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent_surfaces.composition import build_surface_turn_starter
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.onboarding_state import OnboardingStep
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.services.chat_onboarding import (
    ChatOnboardingCoordinator,
)
from app.modules.agent_surfaces.services.onboarding_contact import contact_owner
from app.modules.agent_surfaces.services.onboarding_replay import replay_onboarding
from app.modules.agent_surfaces.services.onboarding_replies import (
    NO_EMAIL_SIGNUP_MESSAGE,
)
from app.modules.agent_surfaces.tests.e2e.helpers import _telegram_payload
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    run_scripted_agent_run,
    script_text,
)
from app.modules.identity.infrastructure.models.user_models import User

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

EMAIL_PROMPT = "What's your email address?"


@pytest.fixture
def telegram(fake_telegram, monkeypatch):
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )
    return fake_telegram


@pytest.fixture
def desktop(monkeypatch):
    """Lemma Desktop's settings: unverified matches on, email verification off."""
    monkeypatch.setattr(surface_settings, "surface_allow_unverified_phone_match", True)
    monkeypatch.setattr(settings, "auth_email_verification_required", False)


def _phone() -> str:
    return "15550" + str(int(uuid4().hex[:7], 16)).zfill(9)


async def _user(
    sessions,
    phone: str | None,
    *,
    phone_verified: bool = False,
    email_verified: bool = False,
) -> UUID:
    async with sessions.begin() as session:
        user = User(
            email=f"claim-{uuid4().hex}@gmail.com",
            is_verified=email_verified,
            is_active=True,
            mobile_number=f"+{phone}" if phone else None,
            mobile_verified_at=datetime.now(timezone.utc) if phone_verified else None,
        )
        session.add(user)
        await session.flush()
        return user.id


class _Chat:
    """One Telegram sender talking to a coordinator that cannot send email."""

    def __init__(self, db_session, *, email_deliverable: bool = False) -> None:
        self.sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
        self.factory = SessionUnitOfWorkFactory(self.sessions)
        self.coordinator = ChatOnboardingCoordinator(
            self.factory, email_deliverable=lambda: email_deliverable
        )
        self.actor = int(uuid4().hex[:8], 16)

    async def say(self, text: str, *, contact: str | None = None, owner=None):
        payload = _telegram_payload(
            text=text, message_id=int(uuid4().hex[:7], 16), sender_id=self.actor
        )
        payload["message"]["from"].pop("username")
        if contact is not None:
            payload["message"].pop("text")
            payload["message"]["contact"] = {
                "user_id": self.actor if owner is None else owner,
                "phone_number": contact,
                "first_name": "Sender",
            }
        return await self.coordinator.handle(
            SurfacePlatformWebhookIngress(source="telegram", payload=payload)
        )

    async def bound_user(self) -> UUID | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(VerifiedSurfaceIdentity).where(
                    VerifiedSurfaceIdentity.external_user_id == str(self.actor),
                    VerifiedSurfaceIdentity.revoked_at.is_(None),
                )
            )
            return row.user_id if row is not None else None

    async def pending(self) -> PendingChatOnboarding:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(PendingChatOnboarding)
                    .where(PendingChatOnboarding.platform == "TELEGRAM")
                    .order_by(PendingChatOnboarding.created_at.desc())
                )
            ).all()
            return next(
                row
                for row in rows
                if (row.destination or {}).get("sender_external_user_id")
                == str(self.actor)
            )


def _said(message_store) -> list[str]:
    return [str(item) for item in message_store.get_all("TELEGRAM")]


async def test_an_unverified_profile_number_links_the_chat_and_reaches_the_agent(
    async_client, db_session, telegram, message_store, desktop
):
    chat = _Chat(db_session)
    phone = _phone()
    user_id = await _user(chat.sessions, phone)

    assert (await chat.say("Plan my week")).handled
    assert (await chat.say("", contact=phone)).handled

    assert await chat.bound_user() == user_id
    async with chat.sessions() as session:
        user = await session.get(User, user_id)
        # The shared contact proved the number, so it is verified now.
        assert user.mobile_verified_at is not None
    assert not any(EMAIL_PROMPT in text for text in _said(message_store))

    pending = await chat.pending()
    assert pending.step == OnboardingStep.READY and pending.user_id == user_id
    queue = AsyncMock(spec=SharedStreaqJobQueue)
    await replay_onboarding(pending.id, uow_factory=chat.factory, job_queue=queue)
    context = SurfaceChatContext.model_validate(
        queue.enqueue.call_args.kwargs["payload"]["context"]
    )
    assert context.message_text == "Plan my week" and context.user_id == user_id
    with suppress_agent_run_enqueue():
        await build_surface_turn_starter(chat.factory).execute_chat(context)
    await run_scripted_agent_run(
        db_session,
        conversation_id=context.conversation_id,
        user_id=context.user_id,
        pod_id=context.pod_id,
        agent_name=context.agent_name,
        script=[script_text("Your week is planned")],
    )
    assert any("Your week is planned" in text for text in _said(message_store))
    # The next message is no longer signup's: it goes straight to routing.
    assert not (await chat.say("And next week?")).handled


async def test_with_the_switch_off_an_unverified_number_matches_nobody(
    async_client, db_session, telegram, message_store, monkeypatch
):
    monkeypatch.setattr(settings, "auth_email_verification_required", False)
    monkeypatch.setattr(surface_settings, "surface_allow_unverified_phone_match", False)
    chat = _Chat(db_session, email_deliverable=True)
    phone = _phone()
    await _user(chat.sessions, phone)

    assert (await chat.say("Hello")).handled
    assert (await chat.say("", contact=phone)).handled

    assert await chat.bound_user() is None
    assert any(EMAIL_PROMPT in text for text in _said(message_store))


@pytest.mark.parametrize("how", ["typed", "someone_else"])
async def test_a_typed_or_borrowed_contact_proves_nothing(
    async_client, db_session, telegram, message_store, desktop, how
):
    chat = _Chat(db_session)
    phone = _phone()
    await _user(chat.sessions, phone)

    assert (await chat.say("Hello")).handled
    if how == "typed":
        assert (await chat.say("+" + phone)).handled
    else:
        assert (await chat.say("", contact=phone, owner=chat.actor + 1)).handled

    assert await chat.bound_user() is None
    assert "Use Share my contact" in _said(message_store)[-1]
    assert (await chat.pending()).step == OnboardingStep.AWAITING_PHONE


async def test_a_verified_owner_wins_over_a_squatters_claim(
    async_client, db_session, telegram, message_store, desktop
):
    chat = _Chat(db_session)
    phone = _phone()
    squatter = await _user(chat.sessions, phone)
    owner = await _user(chat.sessions, phone, phone_verified=True)

    # The verified owner is who the contact names, never the claim beside it.
    assert await contact_owner(chat.factory, "+" + phone) == owner

    assert (await chat.say("Hello")).handled
    assert (await chat.say("", contact=phone)).handled
    # Profile saves refuse a number another profile holds, so two holders is
    # legacy data; provisioning refuses it too rather than pick. Either way the
    # squatter never gets the chat.
    assert await chat.bound_user() != squatter


async def test_a_squatters_claim_never_matches_when_two_profiles_claim_it(
    async_client, db_session, telegram, message_store, desktop
):
    chat = _Chat(db_session)
    phone = _phone()
    await _user(chat.sessions, phone)
    await _user(chat.sessions, phone)

    assert (await chat.say("Hello")).handled
    assert (await chat.say("", contact=phone)).handled

    assert await chat.bound_user() is None
    assert NO_EMAIL_SIGNUP_MESSAGE in _said(message_store)[-1]


async def test_without_email_a_stranger_is_refused_rather_than_asked_for_one(
    async_client, db_session, telegram, message_store, desktop
):
    chat = _Chat(db_session)

    assert (await chat.say("Hello")).handled
    assert (await chat.say("", contact=_phone())).handled

    said = _said(message_store)
    assert NO_EMAIL_SIGNUP_MESSAGE in said[-1]
    assert not any(EMAIL_PROMPT in text for text in said)
    pending = await chat.pending()
    assert pending.step == OnboardingStep.REFUSED
    assert pending.handed_off_at is not None


@pytest.mark.parametrize("required", [False, True])
async def test_an_unverified_email_counts_only_where_the_server_requires_it(
    async_client, db_session, telegram, message_store, monkeypatch, required
):
    monkeypatch.setattr(surface_settings, "surface_allow_unverified_phone_match", True)
    monkeypatch.setattr(settings, "auth_email_verification_required", required)
    chat = _Chat(db_session)
    phone = _phone()
    user_id = await _user(chat.sessions, phone, phone_verified=True)

    assert (await chat.say("Hello")).handled
    assert (await chat.say("", contact=phone)).handled

    if required:
        assert await chat.bound_user() is None
        assert NO_EMAIL_SIGNUP_MESSAGE in _said(message_store)[-1]
    else:
        assert await chat.bound_user() == user_id
