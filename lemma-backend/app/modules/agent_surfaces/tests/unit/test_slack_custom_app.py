from __future__ import annotations

import hashlib
import hmac
import time
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfaceMode,
)
from app.modules.agent_surfaces.platforms.common import computed_webhook_url
from app.modules.agent_surfaces.services.webhook_security_service import (
    SurfaceWebhookSecurityService,
)

pytestmark = pytest.mark.asyncio

OWN_SECRET = "the-orgs-own-signing-secret"
DEPLOYMENT_SECRET = "lemmas-own-signing-secret"


def _surface(*, webhook_secret: str | None) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="slack",
        agent_id=uuid4(),
        surface_type="SLACK",
        mode=SurfaceMode.DM,
        account_id=uuid4(),
        config=SurfaceConfig(),
        is_active=True,
        webhook_secret=webhook_secret,
    )


def _signed(secret: str, body: bytes) -> dict[str, str]:
    ts = str(int(time.time()))
    digest = hmac.new(
        secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256
    ).hexdigest()
    return {"x-slack-request-timestamp": ts, "x-slack-signature": f"v0={digest}"}


async def test_a_workspaces_own_slack_app_verifies_against_its_own_secret(monkeypatch):
    """Bring-your-own Slack app signs with a secret the deployment never sees.

    Without this the shared platform check runs, the deployment's secret fails
    the HMAC, and every event from that app is rejected — which is why this
    looked architecturally impossible rather than simply unfinished.
    """
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "slack_signing_secret", DEPLOYMENT_SECRET)
    body = b'{"type":"event_callback"}'

    await SurfaceWebhookSecurityService().verify_surface_request(
        surface=_surface(webhook_secret=OWN_SECRET),
        headers=_signed(OWN_SECRET, body),
        raw_body=body,
    )


async def test_the_deployments_secret_cannot_sign_for_a_custom_app(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "slack_signing_secret", DEPLOYMENT_SECRET)
    body = b'{"type":"event_callback"}'

    with pytest.raises(Exception):
        await SurfaceWebhookSecurityService().verify_surface_request(
            surface=_surface(webhook_secret=OWN_SECRET),
            headers=_signed(DEPLOYMENT_SECRET, body),
            raw_body=body,
        )


async def test_a_surface_without_its_own_secret_still_uses_the_deployments(monkeypatch):
    """The shared Lemma app must keep working exactly as before."""
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "slack_signing_secret", DEPLOYMENT_SECRET)
    body = b'{"type":"event_callback"}'

    await SurfaceWebhookSecurityService().verify_surface_request(
        surface=_surface(webhook_secret=None),
        headers=_signed(DEPLOYMENT_SECRET, body),
        raw_body=body,
    )


async def test_a_custom_app_is_pointed_at_its_own_webhook_url(monkeypatch):
    """A custom app must not deliver to the shared endpoint.

    That endpoint verifies against the deployment's secret, so events sent
    there would be rejected however correct the app is.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.common.public_https_api_url_available",
        lambda: True,
    )

    own = _surface(webhook_secret=OWN_SECRET)
    shared = _surface(webhook_secret=None)

    assert computed_webhook_url(own) == (
        f"https://api.example.test/surfaces/{own.id}/webhook"
    )
    assert computed_webhook_url(shared) == (
        "https://api.example.test/surfaces/webhooks/slack"
    )
