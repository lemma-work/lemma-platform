"""Which identities still count, which messages carry a link, and who is asked
for an email.

The rules here are small and they decide a lot: whether a Telegram chat linked
without a phone is recognised, whether a `/start` in a group can bind anybody,
and whether a stranger is asked for an address a server can never mail.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.onboarding_state import IdentityProof
from app.modules.agent_surfaces.services.chat_onboarding import (
    ChatOnboardingCoordinator,
)
from app.modules.agent_surfaces.services.onboarding_replies import (
    no_email_signup_message,
)
from app.modules.agent_surfaces.services.telegram_chat_link import link_token_in
from app.modules.agent_surfaces.services.verified_surface_identity import (
    proof_still_holds,
)

pytestmark = pytest.mark.unit

PHONE = "+15550001111"
VERIFIED = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _identity(proof: IdentityProof, phone: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(proof=proof, verified_phone=phone)


def _user(phone: str | None = PHONE, verified: datetime | None = VERIFIED):
    return SimpleNamespace(mobile_number=phone, mobile_verified_at=verified)


@pytest.mark.parametrize(
    ("identity", "user", "holds"),
    [
        # A phone proof is only as good as the number behind it.
        (_identity(IdentityProof.PHONE, PHONE), _user(), True),
        (_identity(IdentityProof.PHONE, PHONE), _user("+15559999999"), False),
        (_identity(IdentityProof.PHONE, PHONE), _user(verified=None), False),
        (_identity(IdentityProof.PHONE, None), _user(), False),
        # The others proved the account itself, so a number is beside the point.
        (_identity(IdentityProof.EMAIL), _user(None, None), True),
        (_identity(IdentityProof.LINK_TOKEN), _user(None, None), True),
        (_identity(IdentityProof.LINK_TOKEN), _user("+15559999999"), True),
    ],
)
def test_only_a_phone_proof_is_held_to_the_accounts_number(identity, user, holds):
    assert proof_still_holds(identity, user) is holds


def _event(
    text: str,
    *,
    platform: SurfacePlatform = SurfacePlatform.TELEGRAM,
    is_dm: bool = True,
) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=platform,
        external_channel_id="42",
        external_thread_id="42",
        external_message_id="1",
        sender_external_user_id="42",
        message_text=text,
        is_dm=is_dm,
        conversation_type=(
            ConversationType.EXTERNAL_DM if is_dm else ConversationType.EXTERNAL_GROUP
        ),
    )


def test_a_private_start_with_a_link_carries_its_token():
    assert link_token_in(_event("/start link_abcdefghijklmnop")) == "abcdefghijklmnop"


def test_a_link_pasted_into_a_group_binds_nobody():
    """In a group `/start` is anyone's to send; the first to tap would win."""
    assert link_token_in(_event("/start link_abcdefghijklmnop", is_dm=False)) is None


def test_only_telegram_reads_a_start_link():
    event = _event("/start link_abcdefghijklmnop", platform=SurfacePlatform.WHATSAPP)
    assert link_token_in(event) is None


def test_a_plain_start_is_left_to_signup():
    assert link_token_in(_event("/start")) is None


@pytest.mark.parametrize(
    ("deliverable", "challenges", "asks"),
    [
        (None, None, False),  # a spool is not an inbox
        (None, object(), True),  # a challenge service handed in delivers itself
        (lambda: True, None, True),
        (lambda: False, object(), False),  # an explicit answer wins
    ],
)
def test_email_is_asked_for_only_when_a_code_could_arrive(
    deliverable, challenges, asks, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "email_transport", "filesystem")
    coordinator = ChatOnboardingCoordinator(
        uow_factory=None,  # type: ignore[arg-type]  # never opened here
        challenges=challenges,  # type: ignore[arg-type]
        event_dedup_store=object(),  # type: ignore[arg-type]
        email_deliverable=deliverable,
    )
    assert coordinator._can_email() is asks


def test_a_stranger_without_email_is_told_who_can_let_them_in():
    message = no_email_signup_message(SurfacePlatform.WHATSAPP)
    assert "private Lemma" in message
    assert "owner" in message
    assert "email" not in message.lower()


def test_the_telegram_refusal_also_shows_the_owner_their_own_way_in():
    message = no_email_signup_message(SurfacePlatform.TELEGRAM)
    assert "Server setup" in message
    assert "Chat with your agents on Telegram" in message
