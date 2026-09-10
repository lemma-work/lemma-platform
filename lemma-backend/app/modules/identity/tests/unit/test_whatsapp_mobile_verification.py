from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.modules.agent_surfaces.contracts.whatsapp import GlobalWhatsAppDeliveryError
from app.core.helpers.identifiers import normalize_mobile_e164
from app.modules.agent_surfaces.config import surface_settings
from app.modules.identity.services.whatsapp_mobile_verification import (
    WhatsAppMobileVerificationService,
    parse_reserved_verification_message,
)
from app.modules.identity.config import identity_settings


def _payload(
    *,
    body: str = "LEMMA VERIFY 23456789AB",
    sender: str = "14155552671",
    destination: str = "phone-id",
    message_id: str = "wamid.123",
) -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": destination},
                            "messages": [
                                {
                                    "id": message_id,
                                    "from": sender,
                                    "type": "text",
                                    "text": {"body": body},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }


def test_normalize_mobile_e164_requires_explicit_country_code() -> None:
    assert normalize_mobile_e164("+1 (415) 555-2671") == "+14155552671"

    for invalid in (
        "14155552671",
        "+0123456789",
        "+123",
        "+1CALL5552671",
        "++14155552671",
        "not-a-number",
    ):
        try:
            normalize_mobile_e164(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected {invalid!r} to be rejected")


def test_parse_reserved_verification_message_intercepts_reserved_attempts() -> None:
    assert parse_reserved_verification_message(_payload()) == (
        "23456789AB",
        "14155552671",
        "phone-id",
        "wamid.123",
    )

    assert (
        parse_reserved_verification_message(_payload(body="lemma verify 23456789AB"))
        is None
    )
    assert parse_reserved_verification_message(
        _payload(body="LEMMA VERIFY 23456789AB ")
    ) == (
        "23456789AB ",
        "14155552671",
        "phone-id",
        "wamid.123",
    )
    assert parse_reserved_verification_message(
        _payload(body="LEMMA VERIFY 1111111111")
    ) == (
        "1111111111",
        "14155552671",
        "phone-id",
        "wamid.123",
    )
    assert parse_reserved_verification_message(
        _payload(body=f"LEMMA VERIFY {'A' * 65}")
    ) == ("", "14155552671", "phone-id", "wamid.123")
    assert parse_reserved_verification_message({}) is None


@pytest.mark.asyncio
async def test_whatsapp_verification_configuration_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "phone-id")
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", "app-secret")
    monkeypatch.setattr(surface_settings, "whatsapp_verify_token", "verify-token")
    monkeypatch.setattr(
        surface_settings, "whatsapp_display_phone_number", "+14155550000"
    )
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    service = WhatsAppMobileVerificationService("redis://unused")

    monkeypatch.setattr(
        identity_settings, "auth_whatsapp_mobile_verification_enabled", False
    )
    assert (await service.config()).available is False

    monkeypatch.setattr(
        identity_settings, "auth_whatsapp_mobile_verification_enabled", True
    )
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", False)
    assert (await service.config()).available is False

    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    config = await service.config()
    assert config.available is True
    assert config.display_number == "+14155550000"


@pytest.mark.asyncio
async def test_enable_flag_without_global_surface_config_stays_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        identity_settings, "auth_whatsapp_mobile_verification_enabled", True
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", None)
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", None)
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", None)
    monkeypatch.setattr(surface_settings, "whatsapp_verify_token", None)
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)

    config = await WhatsAppMobileVerificationService("redis://unused").config()

    assert config.available is False


@pytest.mark.asyncio
async def test_whatsapp_feedback_failure_never_reverts_verification() -> None:
    feedback_sender = AsyncMock(
        side_effect=GlobalWhatsAppDeliveryError("Meta is unavailable")
    )
    service = WhatsAppMobileVerificationService(
        "redis://unused", feedback_sender=feedback_sender
    )

    with patch(
        "app.modules.identity.services.whatsapp_mobile_verification.logger"
    ) as logger:
        await service._send_feedback(
            sender_wa_id="14155552671",
            whatsapp_message_id="wamid.in",
            succeeded=True,
        )

    logger.warning.assert_called_once_with(
        "identity.mobile_verification.whatsapp.feedback_send_failed",
        outcome="success",
        error_type="GlobalWhatsAppDeliveryError",
    )
