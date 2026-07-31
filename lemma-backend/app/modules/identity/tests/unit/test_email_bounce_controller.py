from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi import HTTPException, Request

from app.modules.identity.api.controllers.email_bounce_controller import (
    verify_bounce_signature,
    verify_resend_webhook_signature,
)


def test_bounce_webhook_signature_is_timestamped_and_tamper_evident():
    body = b'{"email":"person@example.com","event":"hard_bounce"}'
    timestamp = "1700000000"
    secret = "bounce-secret"
    signature = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()

    verify_bounce_signature(
        timestamp=timestamp,
        signature=f"sha256={signature}",
        body=body,
        secret=secret,
        now=1700000100,
    )

    with pytest.raises(HTTPException) as tampered:
        verify_bounce_signature(
            timestamp=timestamp,
            signature=f"sha256={signature}",
            body=body + b" ",
            secret=secret,
            now=1700000100,
        )
    assert tampered.value.status_code == 401

    with pytest.raises(HTTPException) as expired:
        verify_bounce_signature(
            timestamp=timestamp,
            signature=f"sha256={signature}",
            body=body,
            secret=secret,
            now=1700000400,
        )
    assert expired.value.detail == "Webhook timestamp expired"


def test_resend_webhook_signature_is_timestamped_and_tamper_evident():
    body = b'{"type":"email.bounced"}'
    timestamp = "1700000000"
    message_id = "msg_123"
    raw_secret = b"resend-webhook-secret"
    secret = f"whsec_{base64.b64encode(raw_secret).decode()}"
    signed_payload = f"{message_id}.{timestamp}.".encode() + body
    signature = base64.b64encode(
        hmac.new(raw_secret, signed_payload, hashlib.sha256).digest()
    ).decode()

    verify_resend_webhook_signature(
        message_id=message_id,
        timestamp=timestamp,
        signature=f"v1,{signature}",
        body=body,
        secret=secret,
        now=1700000100,
    )

    with pytest.raises(HTTPException) as tampered:
        verify_resend_webhook_signature(
            message_id=message_id,
            timestamp=timestamp,
            signature=f"v1,{signature}",
            body=body + b" ",
            secret=secret,
            now=1700000100,
        )
    assert tampered.value.status_code == 401


def _signed_resend_request(body: bytes, secret: str) -> Request:
    timestamp = str(int(time.time()))
    message_id = "msg_test"
    raw_secret = base64.b64decode(secret.removeprefix("whsec_"))
    signature = base64.b64encode(
        hmac.new(
            raw_secret,
            f"{message_id}.{timestamp}.".encode() + body,
            hashlib.sha256,
        ).digest()
    ).decode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/email/bounces/resend",
            "headers": [
                (b"svix-id", message_id.encode()),
                (b"svix-timestamp", timestamp.encode()),
                (b"svix-signature", f"v1,{signature}".encode()),
            ],
        },
        receive,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bounce_type", "expected"),
    [("Permanent", ["person@example.com"]), ("Temporary", [])],
)
async def test_resend_bounce_deactivates_only_permanent_failures(
    monkeypatch, bounce_type, expected
):
    from app.core.config import settings
    from app.modules.identity.api.controllers import email_bounce_controller

    raw_secret = b"resend-webhook-secret"
    secret = f"whsec_{base64.b64encode(raw_secret).decode()}"
    monkeypatch.setattr(settings, "resend_webhook_secret", secret)
    deactivated: list[str] = []

    async def deactivate(email: str):
        deactivated.append(email)

    monkeypatch.setattr(
        email_bounce_controller, "_deactivate_email_for_hard_bounce", deactivate
    )
    body = json.dumps(
        {
            "type": "email.bounced",
            "data": {
                "to": ["person@example.com"],
                "bounce": {"type": bounce_type},
            },
        },
        separators=(",", ":"),
    ).encode()

    response = await email_bounce_controller.accept_resend_email_bounce(
        _signed_resend_request(body, secret)
    )

    assert response.status_code == 204
    assert deactivated == expected
