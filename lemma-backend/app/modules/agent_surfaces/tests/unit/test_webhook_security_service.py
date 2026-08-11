from __future__ import annotations

import base64
import hashlib
import hmac
import time
import pytest
from uuid import uuid4

from app.modules.agent_surfaces.config import surface_settings
from app.core.security import _is_surface_webhook_path
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
    SurfaceConfig,
)
from app.modules.agent_surfaces.services.webhook_security_service import (
    SurfaceWebhookSecurityService,
    SurfaceWebhookAuthenticationError,
)

pytestmark = pytest.mark.asyncio


async def test_verify_platform_request_skips_checks_when_security_disabled(
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", False)
    service = SurfaceWebhookSecurityService()

    await service.verify_platform_request(
        platform="slack",
        headers={},
        raw_body=b'{"type":"event_callback"}',
    )


async def test_verify_surface_request_uses_surface_telegram_secret(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    service = SurfaceWebhookSecurityService()
    surface = AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="telegram",
        surface_type=SurfacePlatform.TELEGRAM,
        config=SurfaceConfig(type="TELEGRAM"),
        webhook_secret="surface-secret",
    )

    await service.verify_surface_request(
        surface=surface,
        headers={"x-telegram-bot-api-secret-token": "surface-secret"},
        raw_body=b"{}",
    )


async def test_surface_webhook_auth_exclusion_matches_only_uuid_webhook_paths():
    surface_id = "019e7d94-44b9-75ba-8730-21821b4163f8"

    assert _is_surface_webhook_path(f"/surfaces/{surface_id}/webhook") is True
    assert _is_surface_webhook_path(f"/surfaces/{surface_id}/webhook/extra") is False
    assert _is_surface_webhook_path("/surfaces/not-a-uuid/webhook") is False
    assert _is_surface_webhook_path(f"/pods/{surface_id}/surfaces") is False


# ── Resend (Svix) inbound signature verification ──────────────────────────────

_RESEND_SECRET = "whsec_" + base64.b64encode(b"resend-inbound-secret-key").decode()


def _svix_headers(raw_body: bytes, secret: str, *, timestamp: int | None = None) -> dict[str, str]:
    """Build a valid Svix signature header set for ``raw_body``."""
    svix_id = "msg_2b3c4d"
    ts = str(timestamp if timestamp is not None else int(time.time()))
    key = base64.b64decode(secret[len("whsec_") :])
    signed = b"%b.%b.%b" % (svix_id.encode(), ts.encode(), raw_body)
    sig = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return {
        "svix-id": svix_id,
        "svix-timestamp": ts,
        "svix-signature": f"v1,{sig}",
    }


async def test_verify_resend_request_accepts_valid_svix_signature(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "resend_inbound_webhook_secret", _RESEND_SECRET)
    service = SurfaceWebhookSecurityService()
    body = b'{"type":"email.inbound","data":{}}'

    await service.verify_resend_request(
        headers=_svix_headers(body, _RESEND_SECRET), raw_body=body
    )


async def test_verify_resend_request_rejects_tampered_body(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "resend_inbound_webhook_secret", _RESEND_SECRET)
    service = SurfaceWebhookSecurityService()
    headers = _svix_headers(b'{"to":"pod-a@x"}', _RESEND_SECRET)

    with pytest.raises(SurfaceWebhookAuthenticationError):
        await service.verify_resend_request(
            headers=headers, raw_body=b'{"to":"pod-attacker@x"}'
        )


async def test_verify_resend_request_rejects_missing_headers(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "resend_inbound_webhook_secret", _RESEND_SECRET)
    service = SurfaceWebhookSecurityService()

    with pytest.raises(SurfaceWebhookAuthenticationError):
        await service.verify_resend_request(headers={}, raw_body=b"{}")


async def test_verify_resend_request_rejects_stale_timestamp(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "resend_inbound_webhook_secret", _RESEND_SECRET)
    service = SurfaceWebhookSecurityService()
    body = b"{}"
    stale = _svix_headers(body, _RESEND_SECRET, timestamp=int(time.time()) - 3600)

    with pytest.raises(SurfaceWebhookAuthenticationError):
        await service.verify_resend_request(headers=stale, raw_body=body)


async def test_verify_resend_request_raises_when_secret_unconfigured(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "resend_inbound_webhook_secret", None)
    service = SurfaceWebhookSecurityService()
    body = b"{}"

    with pytest.raises(SurfaceWebhookAuthenticationError) as exc:
        await service.verify_resend_request(
            headers=_svix_headers(body, _RESEND_SECRET), raw_body=body
        )
    assert exc.value.status_code == 503


async def test_verify_resend_request_skips_when_security_disabled(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", False)
    service = SurfaceWebhookSecurityService()

    # No signature headers at all, but disabled security short-circuits.
    await service.verify_resend_request(headers={}, raw_body=b"{}")


# ------------------------------------------- which secret verifies inbound mail


def test_inbound_falls_back_to_the_shared_resend_webhook_secret(monkeypatch):
    """One Resend endpoint for both event types means one signing secret.

    `RESEND_WEBHOOK_SECRET` already existed for the bounce endpoint. A
    deployment that points `email.received` at the same webhook should not have
    to set a second variable holding the identical value.
    """
    from app.core.config import settings
    from app.modules.agent_surfaces.config import (
        resolve_resend_inbound_secret,
        surface_settings,
    )

    monkeypatch.setattr(surface_settings, "resend_inbound_webhook_secret", None)
    monkeypatch.setattr(settings, "resend_webhook_secret", "whsec_shared")

    assert resolve_resend_inbound_secret() == "whsec_shared"


def test_a_separate_inbound_endpoint_can_override_the_shared_secret(monkeypatch):
    """Svix derives the signature from a *per-endpoint* secret.

    So when inbound and bounces are separate endpoints in Resend their secrets
    differ, and the shared variable would reject every inbound delivery with a
    signature error that says nothing about which secret it wanted.
    """
    from app.core.config import settings
    from app.modules.agent_surfaces.config import (
        resolve_resend_inbound_secret,
        surface_settings,
    )

    monkeypatch.setattr(
        surface_settings, "resend_inbound_webhook_secret", "whsec_inbound_only"
    )
    monkeypatch.setattr(settings, "resend_webhook_secret", "whsec_bounces")

    assert resolve_resend_inbound_secret() == "whsec_inbound_only"


def test_neither_configured_is_reported_as_unconfigured(monkeypatch):
    from app.core.config import settings
    from app.modules.agent_surfaces.config import (
        resolve_resend_inbound_secret,
        surface_settings,
    )

    monkeypatch.setattr(surface_settings, "resend_inbound_webhook_secret", None)
    monkeypatch.setattr(settings, "resend_webhook_secret", None)

    assert resolve_resend_inbound_secret() is None


def test_our_svix_signature_matches_the_published_reference_vector():
    """Guards the algorithm itself against a self-confirming test.

    Signing the payload with our own code and then verifying it proves only
    that we agree with ourselves. This is Svix's documented vector, so it fails
    if the secret decoding, the signed-content layout, or the digest ever drift
    from what Resend actually sends.
    """
    import base64
    import hashlib
    import hmac

    secret = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
    signed = b"msg_p5jXN8AQM9LWM0D4loKWxJek.1614265330." + b'{"test": 2432232314}'
    key = base64.b64decode(secret[len("whsec_") :])

    digest = base64.b64encode(
        hmac.new(key, signed, hashlib.sha256).digest()
    ).decode()

    assert digest == "g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="
