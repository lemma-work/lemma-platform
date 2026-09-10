"""Composio as a webhook source.

The payload reshaping moved here unchanged from the controller's
`_normalize_composio_payload`. Deliberately unchanged: the point of the move is
that the controller stops knowing which sources exist, and a behaviour change
smuggled in alongside it would be indistinguishable from a regression in the
tests that cover it.

Verification is Composio's own, and goes through this module's published
`contracts.triggers.verify_webhook` even though the implementation now sits a
directory away. Reaching the infrastructure function directly is cheaper — the
contract pulls `ConnectorService` — but that import happens once per process,
behind the `lru_cache` on `get_webhook_source_registry`, and never on the
delivery path. What it would cost is worse: two e2e suites fake Composio's
verification by doubling the published contract, which is connectors' answer to
"is this delivery genuine", and a caller inside connectors taking a shortcut
past it makes that answer untrue for everyone doubling it.

`WebhookSourcePlugin.verify` below is the only port a source has to satisfy.
"""

from __future__ import annotations

from app.modules.connectors.contracts import triggers
from app.modules.schedule.contracts import (
    NormalizedWebhook,
    VerifiedDelivery,
    WebhookDelivery,
    WebhookPayload,
)


class ComposioWebhookSource:
    """Composio's brokered triggers, verified by its own SDK."""

    source = "composio"

    async def verify(self, delivery: WebhookDelivery) -> VerifiedDelivery:
        payload_text = delivery.raw_body.decode("utf-8", errors="replace")
        # No try/except: a verifier that raises anything at all is a delivery
        # that did not verify, and the controller says so once for every plugin
        # rather than each writing the same broad catch.
        # Through the module, not the name: bound at import, a double on the
        # published `verify_webhook` never reaches this call, which is what a
        # `from ... import verify_webhook` here cost — the schedule e2e that
        # fakes Composio's verification got the real SDK and a 403.
        result = await triggers.verify_webhook(payload_text, dict(delivery.headers))
        return VerifiedDelivery(delivery=delivery, payload=_reshape(result))

    async def observe(self, verified: VerifiedDelivery) -> None:
        """Nothing: Composio owns the connection, and tells us about it its own
        way rather than through this endpoint."""

    def normalize(self, verified: VerifiedDelivery) -> NormalizedWebhook | None:
        # Composio's own event id, which it does not reissue on a redelivery.
        event_id = verified.payload.get("id")
        return NormalizedWebhook(
            payload=verified.payload,
            source_event_id=str(event_id) if event_id else None,
        )


def _reshape(verification_result: WebhookPayload) -> WebhookPayload:
    verified_payload = verification_result.get("payload", {})
    raw_payload = verification_result.get("raw_payload", {})
    if not isinstance(verified_payload, dict):
        return {}

    metadata = verified_payload.get("metadata", {})
    connected_account = metadata.get("connected_account", {})
    event_payload = verified_payload.get("payload")
    if not isinstance(event_payload, dict):
        event_payload = raw_payload.get("data", {})

    return {
        "id": raw_payload.get("id", verified_payload.get("id")),
        "timestamp": raw_payload.get("timestamp"),
        "type": verified_payload.get("trigger_slug"),
        "webhook_type": raw_payload.get("type"),
        "metadata": {
            "log_id": raw_payload.get("metadata", {}).get("log_id"),
            "trigger_slug": verified_payload.get("trigger_slug"),
            "trigger_id": verified_payload.get("id"),
            "connected_account_id": connected_account.get("id"),
            "auth_config_id": connected_account.get("auth_config_id"),
            "user_id": verified_payload.get("user_id"),
            "toolkit_slug": verified_payload.get("toolkit_slug"),
            "version": verification_result.get("version"),
        },
        "data": event_payload,
    }
