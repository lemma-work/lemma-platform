"""What signup does when it cannot go forward, and what it says when it does.

Four states a person could be left in, all of them silent or permanent, none of
them visible from inside the conversation:

* a refusal `_complete` had no answer for, left on the step that produced it, so
  every later message was refused in the same words with no exit;
* a revoked platform identity the flow invited back through signup and then
  refused to finish, because "not this account's" and "nobody's" were read as
  the same thing;
* success, which said nothing at all and relied on a replay that can
  dead-letter to be the only sign anything had happened;
* a verified person waiting on an administrator, whose row the purge deleted in
  about a day -- the one state whose entire purpose is to outlast a human.

The shared bot is the setting throughout because it is the one that needs no
installation to exist: a number carries the message without belonging to any
workspace, which is exactly the stranger case signup is for.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.onboarding_state import OnboardingStep
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.platforms.whatsapp.adapter import WhatsAppSurfaceAdapter
from app.modules.agent_surfaces.services.chat_onboarding import (
    READY_MESSAGE,
    ChatOnboardingCoordinator,
)
from app.modules.agent_surfaces.services.onboarding_cleanup import (
    ATTACHED_PURGE_GRACE_SECONDS,
    PURGE_GRACE_SECONDS,
    purge_expired_onboarding,
)
from app.modules.agent_surfaces.tests.e2e.helpers import _whatsapp_payload
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.services.email_challenges import EmailChallengeService
from app.modules.identity.tests.e2e.test_email_challenges_e2e import allow_test_delivery

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.fixture
def whatsapp_signup(db_session, fake_whatsapp, message_store, monkeypatch):
    """A stranger on the shared number, and the codes they are sent.

    Returns `(say, sessions, codes, sender_phone)`. `say` drives one real
    inbound webhook through the coordinator, so every row these assert on was
    written by the code under test rather than by the test.
    """
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.whatsapp.service._WHATSAPP_API_BASE",
        f"{fake_whatsapp.api_base}/v21.0",
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-recovery")
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
    )
    sender = "15550" + str(uuid4().int)[:9]

    async def say(text: str):
        return await coordinator.handle(
            SurfacePlatformWebhookIngress(
                source="whatsapp",
                payload=_whatsapp_payload(
                    text=text,
                    message_id=uuid4().hex,
                    phone_number_id=surface_settings.whatsapp_phone_number_id,
                    waba_id=surface_settings.whatsapp_waba_id,
                    sender_phone=sender,
                ),
            )
        )

    return say, sessions, codes, sender


async def _pending(sessions) -> PendingChatOnboarding:
    async with sessions() as session:
        row = await session.scalar(select(PendingChatOnboarding))
        assert row is not None, "signup never opened"
        return row


def _said(message_store) -> list[str]:
    return [str(item.get("text") or item) for item in message_store.get_all("WHATSAPP")]


async def test_a_phone_that_belongs_to_someone_else_ends_the_signup_rather_than_repeating_itself(
    whatsapp_signup, db_session, fixed_test_user, message_store
) -> None:
    """The refusal every re-used number reaches, and used to reach forever.

    The person verifies a mailbox, an account is created for it, and only then
    does provisioning discover that the number they messaged from is already on
    a different Lemma account. That is a real refusal and it has to be said.
    What it must not be is the *only* thing that can ever happen again: the step
    was left on VERIFIED, so the next message walked back into `_complete`, was
    refused in the same words, and there was no message they could send that
    meant anything else.
    """
    say, sessions, codes, sender = whatsapp_signup
    async with sessions() as session:
        owner = await session.get(User, fixed_test_user["id"])
        # Unverified on purpose: identity resolution only matches a *proven*
        # number, so this does not short out signup -- it waits at the one
        # place that checks every number, which is where the refusal lives.
        owner.mobile_number = f"+{sender}"
        await session.commit()

    await say("can you look at this invoice")
    await say(f"recovery-{uuid4().hex}@gmail.com")
    await say(codes[0])

    refusal = "This phone belongs to another account"
    assert any(refusal in text for text in _said(message_store)), (
        "the person was never told why setup stopped"
    )
    stopped = await _pending(sessions)
    assert stopped.step == OnboardingStep.REFUSED
    assert stopped.handed_off_at is not None, "the next message re-enters the refusal"

    said_before = len(_said(message_store))
    await say("what now?")

    assert refusal not in _said(message_store)[said_before], (
        "the refusal answered the next message too, which is the loop itself"
    )
    reopened = await _pending(sessions)
    assert reopened.step != OnboardingStep.REFUSED, (
        "nothing a person can send moves this row"
    )


async def test_a_revoked_identity_can_be_bound_to_the_account_that_proves_it_next(
    whatsapp_signup, db_session, fixed_test_user, message_store
) -> None:
    """The flow invited them back through signup and then refused to finish it.

    `onboarding_sender` reads a revoked identity as an unrecognised sender and
    sends them through a fresh signup deliberately -- a number gets re-issued, a
    work account gets handed on, and the platform actor is the same string while
    the person is not. Completion then refused on `user_id != user.id` without
    looking at `revoked_at`, so the two halves of the same flow disagreed about
    what a revoked row means, and the person paid for it by proving a mailbox
    and being told no.
    """
    say, sessions, codes, sender = whatsapp_signup
    await say("hello again")
    started = await _pending(sessions)
    async with sessions() as session:
        session.add(
            VerifiedSurfaceIdentity(
                binding_key=started.binding_key,
                platform="WHATSAPP",
                tenant_id=surface_settings.whatsapp_waba_id,
                external_user_id=sender,
                user_id=fixed_test_user["id"],
                verified_phone="+19995550000",
                revoked_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    email = f"revoked-{uuid4().hex}@gmail.com"
    await say(email)
    await say(codes[0])

    async with sessions() as session:
        arrived = await session.scalar(select(User).where(User.email == email))
        assert arrived is not None, "the second person never got an account"
        identity = await session.scalar(
            select(VerifiedSurfaceIdentity).where(
                VerifiedSurfaceIdentity.binding_key == started.binding_key
            )
        )
        assert identity.user_id == arrived.id, (
            "a revoked identity was still treated as somebody else's"
        )
        assert identity.revoked_at is None
        # Nothing of the previous owner's survives the reassignment: their
        # number on this row would be matched on every later WhatsApp message.
        assert identity.verified_phone == f"+{sender}"
    assert "belongs to another account" not in " ".join(_said(message_store))


async def test_a_live_identity_still_belongs_to_the_account_that_proved_it(
    whatsapp_signup, db_session, fixed_test_user, message_store
) -> None:
    """The half that must not move, or the fix is an account takeover.

    Only `revoked_at` distinguishes "nobody's" from "somebody else's", so the
    live case is pinned beside the revoked one rather than left implied.
    """
    say, sessions, codes, sender = whatsapp_signup
    await say("hello")
    started = await _pending(sessions)
    async with sessions() as session:
        session.add(
            VerifiedSurfaceIdentity(
                binding_key=started.binding_key,
                platform="WHATSAPP",
                tenant_id=surface_settings.whatsapp_waba_id,
                external_user_id=sender,
                user_id=fixed_test_user["id"],
            )
        )
        await session.commit()

    email = f"live-{uuid4().hex}@gmail.com"
    await say(email)
    await say(codes[0])

    async with sessions() as session:
        identity = await session.scalar(
            select(VerifiedSurfaceIdentity).where(
                VerifiedSurfaceIdentity.binding_key == started.binding_key
            )
        )
        assert str(identity.user_id) == fixed_test_user["id"]
    assert any("belongs to another account" in text for text in _said(message_store)), (
        "a live binding was quietly handed to a different account"
    )


async def test_finishing_signup_says_so(whatsapp_signup, message_store) -> None:
    """Success was the only outcome with nothing to show for it.

    The payoff was the replayed request coming back answered, and
    `replay_onboarding` raises in several places that dead-letter after their
    retries -- so a person who had just proved their email could be left with no
    acknowledgement and no answer, which from inside the chat is indistinguish-
    able from having been ignored.
    """
    say, _sessions, codes, _sender = whatsapp_signup
    await say("summarise this thread")
    await say(f"done-{uuid4().hex}@gmail.com")

    await say(codes[0])

    assert any(READY_MESSAGE in text for text in _said(message_store)), (
        "signup finished without telling anybody"
    )


async def test_a_prompt_that_never_arrives_leaves_the_step_where_the_person_last_was(
    whatsapp_signup, message_store, monkeypatch
) -> None:
    """A row may only advance onto a question that was actually asked.

    Advanced to AWAITING_CODE with the prompt undelivered, the person has no
    idea a code is wanted, and the row starts reading whatever they send next
    as one. The inbox's retry cannot help either, because the step it retries
    into is already past the prompt it would have re-sent.
    """
    say, sessions, _codes, _sender = whatsapp_signup
    await say("hello")
    real_send = WhatsAppSurfaceAdapter.send_message

    async def refuse(self, *, credentials, event, message, metadata=None):
        if "six-digit code" in message:
            raise RuntimeError("whatsapp send failed")
        await real_send(
            self,
            credentials=credentials,
            event=event,
            message=message,
            metadata=metadata,
        )

    monkeypatch.setattr(WhatsAppSurfaceAdapter, "send_message", refuse)

    with pytest.raises(RuntimeError, match="whatsapp send failed"):
        await say(f"undelivered-{uuid4().hex}@gmail.com")

    stuck = await _pending(sessions)
    assert stuck.step == OnboardingStep.AWAITING_EMAIL, (
        "the row moved onto a question nobody was asked"
    )
    assert stuck.challenge_id is None

    # And the row means it: what arrives next is read as an address, which is
    # the last thing this person was actually asked for.
    monkeypatch.setattr(WhatsAppSurfaceAdapter, "send_message", real_send)
    await say("123456")

    assert "Send one email address" in _said(message_store)[-1], (
        "a code was accepted for a question that was never asked"
    )


async def _row(sessions, *, user_id, step: str, expired_for: timedelta) -> None:
    async with sessions() as session:
        session.add(
            PendingChatOnboarding(
                binding_key=uuid4().hex,
                platform="WHATSAPP",
                step=step,
                user_id=user_id,
                destination={},
                expires_at=datetime.now(timezone.utc) - expired_for,
            )
        )
        await session.commit()


async def test_a_signup_waiting_on_an_administrator_outlives_the_purge(
    db_session, fixed_test_user
) -> None:
    """The coordinator stops expiring these, and the purge deleted them anyway.

    `_advance` will not expire a row once `user_id` is attached, precisely
    because ORGANIZATION_ACCESS_REQUIRED is a verified person waiting for a
    human to act. The purge read `expires_at` alone, so about a day later the
    row was gone and what came back was "Setup expired" -- to somebody who had
    already done everything asked of them.

    Still bounded, at a month: nobody is coming after that, and a row kept
    forever is somebody's mailbox and phone number kept forever.
    """
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    factory = SessionUnitOfWorkFactory(sessions)
    await _row(
        sessions,
        user_id=fixed_test_user["id"],
        step=OnboardingStep.ORGANIZATION_ACCESS_REQUIRED,
        expired_for=timedelta(seconds=PURGE_GRACE_SECONDS + 3600),
    )
    await _row(
        sessions,
        user_id=None,
        step=OnboardingStep.AWAITING_EMAIL,
        expired_for=timedelta(seconds=PURGE_GRACE_SECONDS + 3600),
    )

    await purge_expired_onboarding(factory)

    async with sessions() as session:
        left = list(await session.scalars(select(PendingChatOnboarding)))
    assert [row.step for row in left] == [
        OnboardingStep.ORGANIZATION_ACCESS_REQUIRED
    ], "the wait the coordinator promised was ended by the purge"

    async with sessions() as session:
        waiting = await session.get(PendingChatOnboarding, left[0].id)
        waiting.expires_at = datetime.now(timezone.utc) - timedelta(
            seconds=ATTACHED_PURGE_GRACE_SECONDS + 3600
        )
        await session.commit()
    await purge_expired_onboarding(factory)

    async with sessions() as session:
        assert await session.scalar(select(PendingChatOnboarding)) is None, (
            "an attached row is kept forever, which is a retention policy nobody chose"
        )
