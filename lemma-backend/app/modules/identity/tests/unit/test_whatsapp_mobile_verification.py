from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.helpers.identifiers import normalize_mobile_e164
from app.modules.identity.services.whatsapp_mobile_verification import (
    WhatsAppMobileVerificationService,
    parse_reserved_verification_message,
)


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


def test_parse_reserved_verification_message_requires_exact_format() -> None:
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
    assert (
        parse_reserved_verification_message(_payload(body="LEMMA VERIFY 23456789AB "))
        is None
    )
    assert (
        parse_reserved_verification_message(_payload(body="LEMMA VERIFY 1111111111"))
        is None
    )
    assert parse_reserved_verification_message({}) is None


@pytest.mark.asyncio
async def test_whatsapp_verification_configuration_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_whatsapp_access_token", "wa-token")
    monkeypatch.setattr(settings, "auth_whatsapp_phone_number_id", "phone-id")
    monkeypatch.setattr(settings, "auth_whatsapp_app_secret", "app-secret")
    monkeypatch.setattr(settings, "auth_whatsapp_verify_token", "verify-token")
    monkeypatch.setattr(settings, "auth_whatsapp_display_phone_number", "+14155550000")
    monkeypatch.setattr(settings, "auth_whatsapp_webhook_security_enabled", True)
    service = WhatsAppMobileVerificationService("redis://unused")

    monkeypatch.setattr(settings, "auth_whatsapp_mobile_verification_enabled", False)
    assert (await service.config()).available is False

    monkeypatch.setattr(settings, "auth_whatsapp_mobile_verification_enabled", True)
    monkeypatch.setattr(settings, "auth_whatsapp_webhook_security_enabled", False)
    assert (await service.config()).available is False

    monkeypatch.setattr(settings, "auth_whatsapp_webhook_security_enabled", True)
    config = await service.config()
    assert config.available is True
    assert config.display_number == "+14155550000"
