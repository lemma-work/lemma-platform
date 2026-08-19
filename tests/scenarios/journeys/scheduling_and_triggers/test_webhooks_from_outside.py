"""Scheduling and triggers → work that starts because something outside said so.

A webhook is the one entry point where the caller cannot sign in. That makes the
ordinary rules inapplicable and the rules that replace them worth testing on
their own: a provider must be able to complete a verification handshake with no
session at all, a delivery must be proved genuine before anything acts on it,
and a delivery nobody is listening for must be absorbed rather than bounced —
because a provider that gets an error retries, and then disables the hook.
"""

from __future__ import annotations

import httpx
import pytest

from harness import capability, covers, journey, proves, scenario

pytestmark = [
    journey("Scheduling and triggers"),
    capability("React to something happening"),
]


@pytest.fixture
async def unauthenticated(stack):
    """A client with no session at all — a provider, not a person."""
    async with httpx.AsyncClient(base_url=stack.base_url, timeout=30.0) as client:
        yield client


@scenario("A provider completes its verification handshake without signing in")
@proves("PS-SCHED-010", "PS-SURF-010")
@covers("surface.webhook.verify")
async def test_verification_needs_no_session(unauthenticated):
    # Lowercase, which is the case the product uses when it registers the hook.
    # The route matches it exactly, so `TELEGRAM` falls through to a refusal —
    # worth knowing, and not what this scenario is about.
    answered = await unauthenticated.get("/surfaces/webhooks/telegram")

    # A provider has no account, so a session requirement here means the hook
    # can never be registered at all — and the failure shows up at setup time,
    # in somebody else's console, with nothing in Lemma to explain it.
    assert answered.status_code == 200, (
        f"a provider could not complete the handshake without signing in "
        f"({answered.status_code}): {answered.text[:300]}"
    )
    assert answered.text.strip() == "ok", (
        f"the handshake answered something a provider will not accept: "
        f"{answered.text[:200]}"
    )


@scenario("A verification challenge is refused when its token is wrong")
@proves("PS-SCHED-010", "PS-SURF-010")
@covers("surface.webhook.verify")
async def test_a_bad_verification_token_is_refused(unauthenticated):
    # WhatsApp's handshake carries a token the deployment has to recognise.
    # Needing no session is not the same as accepting anything: an endpoint that
    # echoed any challenge would let anyone point their own hook at this pod.
    answered = await unauthenticated.get(
        "/surfaces/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.challenge": "lemma-scenarios-challenge",
            "hub.verify_token": "not-the-token-this-deployment-knows",
        },
    )

    assert answered.status_code >= 400, (
        f"an unrecognised verify token was accepted ({answered.status_code}), so "
        f"anyone can register a webhook against this deployment"
    )
    assert "lemma-scenarios-challenge" not in answered.text, (
        "the challenge was echoed back despite the token being wrong"
    )





@scenario("A delivery for an unknown surface is refused rather than guessed at")
@proves("PS-SCHED-010")
@covers("surface.webhook.handle_surface", "surface.webhook.verify_surface")
async def test_a_delivery_to_an_unknown_surface_is_refused(unauthenticated):
    nowhere = "00000000-0000-0000-0000-000000000009"

    delivered = await unauthenticated.post(
        f"/surfaces/{nowhere}/webhook", json={"update_id": 1}
    )

    assert 400 <= delivered.status_code < 500, (
        f"a delivery addressed to a surface that does not exist answered "
        f"{delivered.status_code}: {delivered.text[:300]}"
    )
