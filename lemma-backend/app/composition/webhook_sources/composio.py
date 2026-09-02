"""Composio as a webhook source.

The verification and the payload reshaping are moved here unchanged from
`ComposioWebhookVerifier` and the controller's `_normalize_composio_payload`.
Deliberately unchanged: the point of the move is that the controller stops
knowing which sources exist, and a behaviour change smuggled in alongside it
would be indistinguishable from a regression in the tests that cover it.
"""

from __future__ import annotations

from app.core.log.log import get_logger
from app.modules.schedule.domain.webhook_source import (
    NormalizedWebhook,
    VerifiedDelivery,
    WebhookDelivery,
    WebhookNotVerified,
    WebhookPayload,
)

logger = get_logger(__name__)


class ComposioWebhookSource:
    """Composio's brokered triggers, verified by its own SDK."""

    source = "composio"

    async def verify(self, delivery: WebhookDelivery) -> VerifiedDelivery:
        from app.composition.schedule_connectors import ComposioWebhookVerifier

        payload_text = delivery.raw_body.decode("utf-8", errors="replace")
        try:
            result = await ComposioWebhookVerifier().verify(
                payload_text, dict(delivery.headers)
            )
        except Exception as exc:
            # The reason is diagnostic only. Told to the sender it is a hint at
            # what to fix in the next attempt.
            logger.debug(
                "schedule.webhook_sources.composio.verification_failed.diagnostic",
                error_type=type(exc).__name__,
            )
            raise WebhookNotVerified from exc
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
