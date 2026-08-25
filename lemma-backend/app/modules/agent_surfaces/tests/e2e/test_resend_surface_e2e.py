"""Resend's own idiosyncratic e2e coverage: the raw ``email.received`` webhook
envelope (Svix-style ``{type, data: {...}}``), address-based routing to a
surface, and provisioned-address derivation — none of which the other Resend
e2e coverage (ask_user/display_resource/request_approval/multi-tool-turn
matrix files) exercises, since those all feed an already-normalized payload
directly into ``process_ingress_and_run_scripted``, bypassing the HTTP layer
and ``_normalize_resend_inbound`` entirely.

Like the other platforms, Resend inbound is authenticated: the controller
verifies the Svix signature (HMAC-SHA256 over ``{svix-id}.{svix-timestamp}.{body}``
keyed by ``resend_webhook_secret``) before trusting the payload. These
tests sign their POSTs with ``build_resend_svix_headers`` and set the secret via
``monkeypatch``, mirroring the other platforms' ``build_*_signature_headers``.

The real webhook route only *publishes* an event to the Redis-backed message
bus (there is no consumer wired into the e2e test client), so the raw-webhook
assertions here are purely structural (response message, address matching);
the actual agent-behavior verification uses a second, independent
``process_ingress_and_run_scripted`` call with the already-normalized
equivalent payload and a different message id — avoiding the dedup-key
collision that would occur from double-processing the identical message
(the same class of race that makes
``test_whatsapp_surface_e2e.py``'s webhook+replay pattern occasionally flaky
under a busier test session).
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent_surface,
    _ensure_connector_account,
    _resend_payload,
)
from app.core.config import settings as core_settings
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_resend_svix_headers,
    wait_for_messages,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_text,
)
from app.modules.connectors.domain.connector import AuthProvider

pytestmark = pytest.mark.e2e

# A base64 secret with the Svix ``whsec_`` prefix, matching production shape.
_RESEND_SIGNING_SECRET = "whsec_cmVzZW5kLWUyZS1zaWduaW5nLXNlY3JldA=="


def _raw_resend_envelope(
    *, sender_email: str, to_address: str, message_id: str, text: str, subject: str
) -> dict:
    """A raw Svix-style ``email.received`` envelope, matching the exact shape
    ``_normalize_resend_inbound`` (webhook_controller.py) expects — mirrors
    the unit test fixture in
    ``tests/unit/test_resend_surface.py::test_normalize_resend_inbound_handles_envelope_and_shapes``."""
    return {
        "type": "email.received",
        "data": {
            "from": {"address": sender_email, "name": "Surface Test User"},
            "to": [{"address": to_address}],
            "subject": subject,
            "text": text,
            "headers": [
                {"name": "Message-ID", "value": f"<{message_id}@resend-e2e.test>"},
            ],
        },
    }


async def test_resend_webhook_ignores_unmatched_address(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    fixed_test_user,
    monkeypatch,
):
    """A raw inbound envelope addressed to a mailbox with no active surface is
    ignored (200 OK, no surface/agent involvement) — proves address routing
    fails closed rather than guessing a destination. The Svix signature is valid;
    only the destination is unknown."""
    monkeypatch.setattr(core_settings, "resend_webhook_secret", _RESEND_SIGNING_SECRET)
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.asur.work")
    envelope = _raw_resend_envelope(
        sender_email=fixed_test_user["email"],
        to_address="pod-nonexistent@ops.asur.work",
        message_id="resend-raw-unmatched-1",
        text="Is anyone there?",
        subject="Surface Resend Raw E2E",
    )
    raw_body = json.dumps(envelope).encode("utf-8")
    response = await authenticated_client.post(
        "/surfaces/webhooks/resend",
        content=raw_body,
        headers=build_resend_svix_headers(
            raw_body=raw_body, signing_secret=_RESEND_SIGNING_SECRET
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"message": "Ignored: no surface for address"}


async def test_resend_webhook_rejects_invalid_signature(
    authenticated_client: AsyncClient,
    monkeypatch,
):
    """An inbound envelope with a bad/absent Svix signature is rejected (401)
    before any address routing — proves inbound is authenticated."""
    monkeypatch.setattr(core_settings, "resend_webhook_secret", _RESEND_SIGNING_SECRET)
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.asur.work")
    envelope = _raw_resend_envelope(
        sender_email="attacker@evil.test",
        to_address="pod-anything@ops.asur.work",
        message_id="resend-forged-1",
        text="Forged inbound",
        subject="Forged",
    )
    response = await authenticated_client.post(
        "/surfaces/webhooks/resend",
        content=json.dumps(envelope).encode("utf-8"),
        headers={"Content-Type": "application/json"},  # no Svix signature
    )
    assert response.status_code == 401, response.text


async def test_resend_webhook_routes_raw_envelope_to_provisioned_address(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    """A raw envelope addressed to the surface's own provisioned address is
    accepted and routed (structural: the real route only enqueues, so this
    only proves address derivation + matching, not the downstream agent
    run — that's verified separately below with a normalized-equivalent
    payload and a distinct message id)."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(core_settings, "resend_webhook_secret", _RESEND_SIGNING_SECRET)
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.asur.work")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="resend",
        credentials={
            "api_key": "resend-token",
            "api_base_url": fake_resend.api_base,
        },
        email="assistant@resend.test",
        provider=AuthProvider.LEMMA,
    )
    _agent, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "RESEND", "account_id": str(account.id)},
    )
    assistant_address = surface.get("surface_identity_email")
    if not assistant_address:
        surface_model = await db_session.get(AgentSurface, UUID(surface["id"]))
        assistant_address = surface_model.surface_identity_email
    assert assistant_address
    # Minted per agent by `email_surface_provisioning`, not a fixed constant.
    assert assistant_address.endswith("@ops.asur.work")

    envelope = _raw_resend_envelope(
        sender_email=fixed_test_user["email"],
        to_address=assistant_address,
        message_id="resend-raw-matched-1",
        text="Can you help over email?",
        subject="Surface Resend Raw E2E",
    )
    raw_body = json.dumps(envelope).encode("utf-8")
    response = await authenticated_client.post(
        "/surfaces/webhooks/resend",
        content=raw_body,
        headers=build_resend_svix_headers(
            raw_body=raw_body, signing_secret=_RESEND_SIGNING_SECRET
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"message": "Webhook received"}

    # A distinct message id from the raw envelope above — proves the
    # normalized-equivalent payload drives a real agent run + reply end to
    # end, without colliding with the raw envelope's own dedup key.
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=fixed_test_user["email"],
                assistant_address=assistant_address,
                message_id="resend-raw-matched-1-agent-run",
                text="Can you help over email?",
                subject="Surface Resend Raw E2E",
            ),
            headers={},
        ),
        script=[script_text("E2E agent reply [RESEND]")],
    )
    assert isinstance(context, SurfaceChatContext)

    resend_messages = await wait_for_messages(message_store, "RESEND", min_count=1)
    assert "E2E agent reply [RESEND]" in json.dumps(resend_messages[-1])
