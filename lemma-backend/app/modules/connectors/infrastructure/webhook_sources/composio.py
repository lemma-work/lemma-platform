"""Composio as a webhook source.

The payload reshaping moved here unchanged from the controller's
`_normalize_composio_payload`. Deliberately unchanged: the point of the move is
that the controller stops knowing which sources exist, and a behaviour change
smuggled in alongside it would be indistinguishable from a regression in the
tests that cover it.

Verification is Composio's own, and this now lives beside it: the SDK call is
`connectors.infrastructure.composio_triggers.verify_webhook`, reached directly
rather than through this module's own contracts, which would pull
`ConnectorService` onto a path an external sender chooses the rate of.
`WebhookSourcePlugin.verify` below is the only port a source has to satisfy.
"""

from __future__ import annotations

from app.modules.connectors.infrastructure.composio_triggers import verify_webhook
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
        result = await verify_webhook(payload_text, dict(delivery.headers))
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
