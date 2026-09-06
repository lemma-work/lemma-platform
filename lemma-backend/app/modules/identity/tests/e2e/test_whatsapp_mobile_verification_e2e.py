from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Text, cast, func, select

from app.core.config import settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.events.models import DomainEventOutbox
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_whatsapp_signature_headers,
)
from app.modules.identity.domain.events import WhatsAppMobileVerificationReceivedEvent
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.services.whatsapp_mobile_verification import (
    WhatsAppMobileVerificationService,
    WhatsAppVerificationRateLimited,
    close_whatsapp_mobile_verification_service,
    get_whatsapp_mobile_verification_service,
)
from app.modules.identity.config import identity_settings

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


def _enable(monkeypatch) -> None:
    monkeypatch.setattr(
        identity_settings, "auth_whatsapp_mobile_verification_enabled", True
    )
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "global-phone")
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", "app-secret")
    monkeypatch.setattr(surface_settings, "whatsapp_verify_token", "verify-token")
    monkeypatch.setattr(
        surface_settings, "whatsapp_display_phone_number", "+14155550000"
    )


async def test_whatsapp_transaction_sender_match_single_use_and_cache_invalidation(
    signup_user,
    test_redis_url,
    monkeypatch,
):
    _enable(monkeypatch)
    signed_up = await signup_user(email=f"wa-{uuid4().hex[:8]}@example.com")
    user_id = UUID(signed_up["id"])
    feedback_sender = AsyncMock(return_value=True)
    service = WhatsAppMobileVerificationService(
        test_redis_url, feedback_sender=feedback_sender
    )
    try:
        transaction = await service.start(
            user_id=user_id,
            mobile_number="+1 (415) 555-2671",
            client_key=f"test:{uuid4()}",
        )
        assert transaction.whatsapp_url.startswith("https://wa.me/14155550000")
        assert (
            await service.status(
                transaction_id=transaction.transaction_id, user_id=user_id
            )
            == "PENDING"
        )

        assert not await service.consume_message(
            code="INVALID",
            sender_wa_id="14155552671",
            destination_phone_number_id="global-phone",
            whatsapp_message_id="wamid.invalid-format",
        )
        assert not await service.consume_message(
            code=transaction.code,
            sender_wa_id="14155559999",
            destination_phone_number_id="global-phone",
            whatsapp_message_id="wamid.wrong-sender",
        )
        assert await service.consume_message(
            code=transaction.code,
            sender_wa_id="14155552671",
            destination_phone_number_id="global-phone",
            whatsapp_message_id="wamid.valid",
        )
        assert await service.consume_message(
            code=transaction.code,
            sender_wa_id="14155552671",
            destination_phone_number_id="global-phone",
            whatsapp_message_id="wamid.valid",
        )
        assert (
            await service.status(
                transaction_id=transaction.transaction_id, user_id=user_id
            )
            == "VERIFIED"
        )

        async with async_session_maker() as session:
            user = await session.get(User, user_id)
        assert user is not None
        assert user.mobile_number == "+14155552671"
        assert user.mobile_verified_at is not None

        assert not await service.consume_message(
            code=transaction.code,
            sender_wa_id="14155552671",
            destination_phone_number_id="global-phone",
            whatsapp_message_id="wamid.replay",
        )

        assert feedback_sender.await_count == 5
        invalid_feedback = feedback_sender.await_args_list[0].kwargs
        assert invalid_feedback["to"] == "14155552671"
        assert invalid_feedback["reply_to_message_id"] == "wamid.invalid-format"
        assert "could not verify" in invalid_feedback["body"]
        wrong_sender_feedback = feedback_sender.await_args_list[1].kwargs
        assert wrong_sender_feedback["to"] == "14155559999"
        assert wrong_sender_feedback["reply_to_message_id"] == "wamid.wrong-sender"
        assert "could not verify" in wrong_sender_feedback["body"]
        for index in (2, 3):
            success_feedback = feedback_sender.await_args_list[index].kwargs
            assert success_feedback["to"] == "14155552671"
            assert "verified for Lemma" in success_feedback["body"]
        replay_feedback = feedback_sender.await_args_list[4].kwargs
        assert replay_feedback["reply_to_message_id"] == "wamid.replay"
        assert "could not verify" in replay_feedback["body"]
    finally:
        await service.close()


async def test_whatsapp_transaction_without_a_declared_number_binds_the_sender(
    signup_user,
    test_redis_url,
    monkeypatch,
):
    """The connect journey mints a code for someone who has typed no number.

    Nothing to match the sender against, so whichever phone sends the code is
    the phone that gets bound -- the same rule Telegram's OIDC claim follows,
    and the same number the declared flow would have written.
    """
    _enable(monkeypatch)
    signed_up = await signup_user(email=f"wa-open-{uuid4().hex[:8]}@example.com")
    user_id = UUID(signed_up["id"])
    feedback_sender = AsyncMock(return_value=True)
    service = WhatsAppMobileVerificationService(
        test_redis_url, feedback_sender=feedback_sender
    )
    try:
        transaction = await service.start(
            user_id=user_id,
            client_key=f"test:{uuid4()}",
        )
        assert transaction.code in transaction.whatsapp_url

        assert await service.consume_message(
            code=transaction.code,
            sender_wa_id="14155553001",
            destination_phone_number_id="global-phone",
            whatsapp_message_id="wamid.undeclared",
        )
        assert (
            await service.status(
                transaction_id=transaction.transaction_id, user_id=user_id
            )
            == "VERIFIED"
        )

        async with async_session_maker() as session:
            user = await session.get(User, user_id)
        assert user is not None
        assert user.mobile_number == "+14155553001"
        assert user.mobile_verified_at is not None

        # Single use survives the missing number: the code is spent, and a
        # second sender cannot take the transaction off the first one.
        assert not await service.consume_message(
            code=transaction.code,
            sender_wa_id="14155553002",
            destination_phone_number_id="global-phone",
            whatsapp_message_id="wamid.undeclared-second",
        )
    finally:
        await service.close()


async def test_whatsapp_transaction_expires(
    signup_user,
    test_redis_url,
    monkeypatch,
):
    _enable(monkeypatch)
    signed_up = await signup_user(email=f"wa-expiry-{uuid4().hex[:8]}@example.com")
    user_id = UUID(signed_up["id"])
    service = WhatsAppMobileVerificationService(test_redis_url, ttl_seconds=1)
    try:
        transaction = await service.start(
            user_id=user_id,
            mobile_number="+14155552672",
            client_key=f"test:{uuid4()}",
        )
        await asyncio.sleep(1.1)
        assert (
            await service.status(
                transaction_id=transaction.transaction_id, user_id=user_id
            )
            == "EXPIRED"
        )
    finally:
        await service.close()


async def test_whatsapp_start_is_rate_limited_and_bound_to_one_active_transaction(
    signup_user,
    test_redis_url,
    monkeypatch,
):
    _enable(monkeypatch)
    signed_up = await signup_user(email=f"wa-limit-{uuid4().hex[:8]}@example.com")
    other_user = await signup_user(email=f"wa-other-{uuid4().hex[:8]}@example.com")
    user_id = UUID(signed_up["id"])
    service = WhatsAppMobileVerificationService(test_redis_url)
    transactions = []
    try:
        for index in range(5):
            transactions.append(
                await service.start(
                    user_id=user_id,
                    mobile_number=f"+14155552{index:03d}",
                    client_key="203.0.113.8",
                )
            )

        assert (
            await service.status(
                transaction_id=transactions[0].transaction_id,
                user_id=user_id,
            )
            == "EXPIRED"
        )
        assert (
            await service.status(
                transaction_id=transactions[-1].transaction_id,
                user_id=UUID(other_user["id"]),
            )
            == "EXPIRED"
        )
        assert (
            await service.status(
                transaction_id=transactions[-1].transaction_id,
                user_id=user_id,
            )
            == "PENDING"
        )

        with pytest.raises(WhatsAppVerificationRateLimited) as limited:
            await service.start(
                user_id=user_id,
                mobile_number="+141555529999",
                client_key="203.0.113.8",
            )
        assert limited.value.retry_after_seconds > 0
    finally:
        await service.close()


async def test_whatsapp_api_rejects_a_number_claimed_by_another_profile(
    authenticated_client,
    signup_user,
    monkeypatch,
):
    _enable(monkeypatch)
    phone = "+14155552679"
    owner = await signup_user(email=f"wa-owner-{uuid4().hex[:8]}@example.com")
    async with async_session_maker() as session:
        owner_model = await session.get(User, UUID(owner["id"]))
        assert owner_model is not None
        owner_model.mobile_number = phone
        owner_model.mobile_verified_at = None
        await session.commit()

    response = await authenticated_client.post(
        "/auth/mobile-verification/whatsapp/start",
        json={"mobile_number": phone},
    )

    assert response.status_code == 409
    assert response.json()["message"] == "This mobile number is already in use"


async def test_authenticated_whatsapp_verification_api_journey(
    authenticated_client,
    fixed_test_user,
    e2e_settings,
    monkeypatch,
):
    _enable(monkeypatch)
    monkeypatch.setattr(settings, "redis_url", e2e_settings.redis_url)
    feedback_sender = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.modules.identity.services.whatsapp_mobile_verification.send_global_whatsapp_text",
        feedback_sender,
    )
    await close_whatsapp_mobile_verification_service()
    try:
        config = await authenticated_client.get(
            "/auth/mobile-verification/whatsapp/config"
        )
        assert config.status_code == 200, config.text
        assert config.json() == {
            "available": True,
            "display_number": "+14155550000",
        }

        started = await authenticated_client.post(
            "/auth/mobile-verification/whatsapp/start",
            json={"mobile_number": "+14155552673"},
        )
        assert started.status_code == 200, started.text
        transaction = started.json()
        assert transaction["code"] in transaction["whatsapp_url"]

        pending = await authenticated_client.get(
            f"/auth/mobile-verification/whatsapp/status/{transaction['transaction_id']}"
        )
        assert pending.status_code == 200
        assert pending.json()["status"] == "PENDING"

        service = get_whatsapp_mobile_verification_service()
        assert await service.consume_message(
            code=transaction["code"],
            sender_wa_id="14155552673",
            destination_phone_number_id="global-phone",
            whatsapp_message_id="wamid.api-journey",
        )

        verified = await authenticated_client.get(
            f"/auth/mobile-verification/whatsapp/status/{transaction['transaction_id']}"
        )
        assert verified.status_code == 200
        assert verified.json()["status"] == "VERIFIED"
        async with async_session_maker() as session:
            user = await session.get(User, UUID(str(fixed_test_user["id"])))
        assert user is not None
        assert user.mobile_number == "+14155552673"
        assert user.mobile_verified_at is not None
        feedback_sender.assert_awaited_once()
        assert feedback_sender.await_args.kwargs["reply_to_message_id"] == (
            "wamid.api-journey"
        )
    finally:
        await close_whatsapp_mobile_verification_service()


async def test_signed_whatsapp_webhook_is_deduplicated_and_never_becomes_surface_event(
    async_client,
    signup_user,
    test_redis_url,
    monkeypatch,
):
    _enable(monkeypatch)
    signed_up = await signup_user(email=f"wa-webhook-{uuid4().hex[:8]}@example.com")
    service = WhatsAppMobileVerificationService(test_redis_url)
    try:
        transaction = await service.start(
            user_id=UUID(signed_up["id"]),
            mobile_number="+14155552674",
            client_key=f"test:{uuid4()}",
        )
        message_id = f"wamid.signed-{uuid4().hex}"
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "global-phone"},
                                "messages": [
                                    {
                                        "id": message_id,
                                        "from": "14155552674",
                                        "type": "text",
                                        "text": {
                                            "body": f"LEMMA VERIFY {transaction.code}"
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }
        raw_body = json.dumps(payload).encode()
        headers = build_whatsapp_signature_headers(
            raw_body=raw_body, app_secret="app-secret"
        )
        first = await async_client.post(
            "/surfaces/webhooks/whatsapp", content=raw_body, headers=headers
        )
        second = await async_client.post(
            "/surfaces/webhooks/whatsapp", content=raw_body, headers=headers
        )
        assert first.status_code == second.status_code == 200
        assert first.json()["message"] == "Verification message received"

        async with async_session_maker() as session:
            identity_events = await session.scalar(
                select(func.count())
                .select_from(DomainEventOutbox)
                .where(
                    DomainEventOutbox.event_type
                    == WhatsAppMobileVerificationReceivedEvent.get_event_type(),
                    DomainEventOutbox.payload["whatsapp_message_id"].astext
                    == message_id,
                )
            )
            surface_events = await session.scalar(
                select(func.count())
                .select_from(DomainEventOutbox)
                .where(
                    DomainEventOutbox.event_type == "surface.webhook.received",
                    cast(DomainEventOutbox.payload, Text).contains(message_id),
                )
            )
        assert identity_events == 1
        assert surface_events == 0
    finally:
        await service.close()
