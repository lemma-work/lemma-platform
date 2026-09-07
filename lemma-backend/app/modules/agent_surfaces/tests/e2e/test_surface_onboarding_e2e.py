"""A stranger on WhatsApp becomes somebody with an account, in four messages.

The whole ladder through the real inbound path: routing, the pending signup in
Redis, the code, SuperTokens, and a workspace at the end. The unit tests cover
what gets said; this covers that the pieces are actually joined to each other.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import SurfaceReplyContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.events.handlers import build_surface_event_handler
from app.modules.agent_surfaces.infrastructure.adapters.redis_onboarding_store import (
    close_surface_onboarding_store,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent_surface,
    _whatsapp_payload,
)
from app.modules.identity.infrastructure.models.user_models import User

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_STRANGER = "15550119001"


@pytest.fixture
def captured_codes(monkeypatch):
    """Read the code out of the mail instead of an inbox."""
    codes: list[dict] = []

    async def capture(self, *, to_email, code, surface_label):
        codes.append({"to": to_email, "code": code, "surface": surface_label})
        return True

    monkeypatch.setattr(
        "app.modules.identity.infrastructure.adapters.email_adapter."
        "SmtpIdentityEmailAdapter.send_chat_signup_code_email",
        capture,
    )
    return codes


async def _say(db_session, text: str, *, message_id: str) -> SurfaceReplyContext | None:
    uow = SqlAlchemyUnitOfWork(db_session)
    handler = build_surface_event_handler(uow)
    context = await handler.prepare_ingress(
        SurfacePlatformWebhookIngress(
            source="whatsapp",
            payload=_whatsapp_payload(
                text=text,
                message_id=message_id,
                phone_number_id="1234567890",
                waba_id="waba-onboarding",
                sender_phone=_STRANGER,
            ),
        )
    )
    await uow.commit()
    return context


async def test_a_stranger_on_whatsapp_talks_their_way_into_an_account(
    authenticated_client,
    db_session,
    test_pod,
    captured_codes,
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-onboarding")
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", "wa-secret")
    await _create_agent_surface(
        authenticated_client, test_pod["id"], config={"type": "WHATSAPP"}
    )
    await close_surface_onboarding_store()

    email = f"ada-{uuid4().hex[:10]}@gmail.com"
    try:
        opening = await _say(
            db_session, "can you check this invoice?", message_id=f"wamid-{uuid4().hex}"
        )
        assert isinstance(opening, SurfaceReplyContext)
        # Not "please sign up" -- a question, which is the whole change.
        assert "email" in opening.reply_message.lower()

        asked = await _say(db_session, email, message_id=f"wamid-{uuid4().hex}")
        assert isinstance(asked, SurfaceReplyContext)
        assert email in asked.reply_message
        assert captured_codes and captured_codes[0]["to"] == email

        done = await _say(
            db_session, captured_codes[0]["code"], message_id=f"wamid-{uuid4().hex}"
        )
        assert isinstance(done, SurfaceReplyContext)
        assert "set up" in done.reply_message.lower()
    finally:
        await close_surface_onboarding_store()

    user = (
        await db_session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    assert user is not None, "the exchange should have produced a real account"
    # The number that talked its way in is theirs now, so the next message
    # resolves normally instead of starting this again.
    assert user.mobile_number == f"+{_STRANGER}"
    assert user.mobile_verified_at is not None
    assert isinstance(user.id, UUID)


async def test_a_wrong_code_does_not_make_an_account(
    authenticated_client,
    db_session,
    test_pod,
    captured_codes,
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-onboarding")
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", "wa-secret")
    await _create_agent_surface(
        authenticated_client, test_pod["id"], config={"type": "WHATSAPP"}
    )
    await close_surface_onboarding_store()

    email = f"mallory-{uuid4().hex[:10]}@gmail.com"
    try:
        await _say(db_session, "hello", message_id=f"wamid-{uuid4().hex}")
        await _say(db_session, email, message_id=f"wamid-{uuid4().hex}")
        refused = await _say(db_session, "AAAAAA", message_id=f"wamid-{uuid4().hex}")
        assert isinstance(refused, SurfaceReplyContext)
        assert "didn't match" in refused.reply_message
    finally:
        await close_surface_onboarding_store()

    user = (
        await db_session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    assert user is None, "an unproven address must not become an account"
