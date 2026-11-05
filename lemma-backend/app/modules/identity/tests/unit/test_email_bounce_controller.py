from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi import HTTPException, Request
from pydantic import SecretStr

from app.core.config import settings
from app.modules.identity.api.controllers.email_bounce_controller import (
    MAX_BOUNCE_BODY_BYTES,
    router,
    verify_bounce_signature,
    verify_resend_webhook_signature,
)
from app.modules.identity.config import identity_settings
from app.modules.test_support.body_ceiling import (
    app_with,
    chunks_of,
    chunks_to_cross,
    deliver,
    past_ceiling,
    too_large,
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


@pytest.mark.parametrize(
    "signature",
    ["sha256=\u00fc", "sha256=" + chr(0xD800)],
    ids=["latin-1", "surrogate"],
)
def test_a_signature_that_is_not_ascii_is_refused_rather_than_raised_on(signature):
    """Attacker-controlled text: a header byte from 0x80 up arrives as a
    non-ASCII `str`, which `hmac.compare_digest` raises on."""
    with pytest.raises(HTTPException) as refused:
        verify_bounce_signature(
            timestamp=str(int(time.time())),
            signature=signature,
            body=b"{}",
            secret="bounce-secret",
        )

    assert refused.value.status_code == 401
    assert refused.value.detail == "Invalid webhook signature"


_BOUNCE_SECRET = "bounce-secret"
_RESEND_SECRET = f"whsec_{base64.b64encode(b'resend-webhook-secret').decode()}"
_ROUTES = ["/auth/email/bounces", "/auth/email/bounces/resend"]


@pytest.fixture
def _configured(monkeypatch):
    monkeypatch.setattr(
        identity_settings, "auth_bounce_webhook_secret", SecretStr(_BOUNCE_SECRET)
    )
    monkeypatch.setattr(settings, "resend_bounce_webhook_secret", None)
    monkeypatch.setattr(settings, "resend_webhook_secret", SecretStr(_RESEND_SECRET))


def _lemma_signed(body: bytes) -> tuple[tuple[bytes, bytes], ...]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        _BOUNCE_SECRET.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return (
        (b"x-lemma-timestamp", timestamp.encode()),
        (b"x-lemma-signature", f"sha256={signature}".encode()),
    )


def _svix_signed(body: bytes) -> tuple[tuple[bytes, bytes], ...]:
    timestamp = str(int(time.time()))
    key = base64.b64decode(_RESEND_SECRET.removeprefix("whsec_"))
    signature = base64.b64encode(
        hmac.new(key, f"msg_test.{timestamp}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    return (
        (b"svix-id", b"msg_test"),
        (b"svix-timestamp", timestamp.encode()),
        (b"svix-signature", f"v1,{signature}".encode()),
    )


@pytest.mark.usefixtures("_configured")
@pytest.mark.parametrize("path", _ROUTES)
async def test_a_declared_length_past_the_ceiling_is_refused_unread(path):
    refused = await deliver(
        app_with(router),
        path,
        [b"\0" * (MAX_BOUNCE_BODY_BYTES + 1)],
        declared_length=MAX_BOUNCE_BODY_BYTES + 1,
    )

    assert refused.status == 413
    assert refused.json() == too_large(MAX_BOUNCE_BODY_BYTES)
    assert refused.chunks_read == 0


@pytest.mark.usefixtures("_configured")
@pytest.mark.parametrize("path", _ROUTES)
async def test_a_chunked_body_with_no_length_is_refused_as_it_crosses(path):
    refused = await deliver(app_with(router), path, past_ceiling(MAX_BOUNCE_BODY_BYTES))

    assert refused.status == 413
    assert refused.json() == too_large(MAX_BOUNCE_BODY_BYTES)
    assert refused.chunks_read == chunks_to_cross(MAX_BOUNCE_BODY_BYTES)


@pytest.mark.usefixtures("_configured")
async def test_a_bounce_is_verified_over_the_bytes_exactly_as_they_arrived():
    """Several chunks with no length; the HMAC is over what was sent, so any
    byte lost or reordered on the way to the check is a 401."""
    body = json.dumps(
        {"email": "person@example.com", "event": "soft_bounce", "pad": "x" * 200_000}
    ).encode()

    answered = await deliver(
        app_with(router), _ROUTES[0], chunks_of(body), headers=_lemma_signed(body)
    )

    assert answered.status == 204, answered.body


@pytest.mark.usefixtures("_configured")
async def test_a_resend_bounce_is_verified_over_the_bytes_exactly_as_they_arrived():
    body = json.dumps(
        {
            "type": "email.bounced",
            "data": {
                "to": ["person@example.com"],
                "bounce": {"type": "Temporary"},
                "pad": "x" * 200_000,
            },
        }
    ).encode()

    answered = await deliver(
        app_with(router), _ROUTES[1], chunks_of(body), headers=_svix_signed(body)
    )

    assert answered.status == 204, answered.body


@pytest.mark.usefixtures("_configured")
async def test_a_non_ascii_signature_header_is_a_401_not_a_500():
    answered = await deliver(
        app_with(router),
        _ROUTES[0],
        [b"{}"],
        headers=(
            (b"x-lemma-timestamp", str(int(time.time())).encode()),
            (b"x-lemma-signature", b"sha256=\xff"),
        ),
    )

    assert answered.status == 401


@pytest.mark.usefixtures("_configured")
async def test_a_bounce_is_parsed_only_after_its_signature_checks_out():
    """Unsigned, a malformed event is a 401 like any other forgery; signed,
    it is the sender's mistake, and a 400."""
    body = b'{"email": "not an address", "event": "hard_bounce"}'

    unsigned = await deliver(app_with(router), _ROUTES[0], [body])
    signed = await deliver(
        app_with(router), _ROUTES[0], [body], headers=_lemma_signed(body)
    )

    assert (unsigned.status, signed.status) == (401, 400)
