"""A connected MCP server as a webhook source.

Every subscription has its own secret, so the tenant has to be found to
verify at all: the callback URL names our subscription, its row holds the
secret, and a delivery that does not verify against that one secret is
refused. The server's own id (`X-MCP-Subscription-Id`) must agree once it has
told us one.

Two kinds of request arrive. A challenge -- `{"type": "verification"}`, sent
while we are still waiting for `events/subscribe` to answer -- is echoed back
and starts nothing. An occurrence is routed by our subscription id, which is
the schedule's `provider_trigger_id`, and its `eventId` is what makes a
redelivery run nothing twice.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from app.core.concurrency.offload import run_blocking
from app.core.log.log import get_logger
from app.core.webhooks.signatures import svix_signature_matches, timestamp_within_skew
from app.modules.connectors.domain.mcp_events import (
    MCP_WEBHOOK_SOURCE,
    SUBSCRIPTION_PARAM,
)
from app.modules.connectors.infrastructure.repositories.mcp_event_repository import (
    StoredEventSubscription,
)
from app.modules.schedule.contracts import (
    NormalizedWebhook,
    VerifiedDelivery,
    WebhookDelivery,
    WebhookNotVerified,
)

logger = get_logger(__name__)

Listening = Callable[
    [UUID], Awaitable[tuple[StoredEventSubscription, SecretStr] | None]
]
Heard = Callable[[UUID], Awaitable[None]]


def _subscription_named(delivery: WebhookDelivery) -> UUID | None:
    try:
        return UUID(str(delivery.query.get(SUBSCRIPTION_PARAM) or ""))
    except ValueError:
        return None


class McpWebhookSource:
    source = MCP_WEBHOOK_SOURCE

    def __init__(
        self, listening: Listening | None = None, heard: Heard | None = None
    ) -> None:
        self._listening = listening
        self._heard = heard

    def _subscriptions(self) -> tuple[Listening, Heard]:
        if self._listening is not None and self._heard is not None:
            return self._listening, self._heard
        from app.modules.connectors.contracts.mcp_events import (
            mcp_event_subscriptions,
        )

        service = mcp_event_subscriptions()
        return service.listening, service.heard

    async def verify(self, delivery: WebhookDelivery) -> VerifiedDelivery:
        subscription_id = _subscription_named(delivery)
        if subscription_id is None:
            raise WebhookNotVerified()
        listening, _ = self._subscriptions()
        found = await listening(subscription_id)
        if found is None:
            raise WebhookNotVerified()
        stored, secret = found
        timestamp = delivery.header("webhook-timestamp")
        if not timestamp_within_skew(timestamp) or not svix_signature_matches(
            delivery.header("webhook-signature"),
            delivery.header("webhook-id") or "",
            timestamp,
            delivery.raw_body,
            [secret.get_secret_value()],
        ):
            raise WebhookNotVerified()
        named = delivery.header("X-MCP-Subscription-Id")
        if stored.remote_id and named and named != stored.remote_id:
            raise WebhookNotVerified()
        try:
            # Off the loop: the sender chooses the body, up to the endpoint's cap.
            payload = await run_blocking(
                json.loads, delivery.raw_body, limiter="cpu_bound"
            )
        except ValueError as exc:
            raise WebhookNotVerified() from exc
        if not isinstance(payload, dict):
            raise WebhookNotVerified()
        if payload.get("type") == "verification":
            challenge = payload.get("challenge")
            if not isinstance(challenge, str):
                raise WebhookNotVerified()
            return VerifiedDelivery(
                delivery=delivery,
                payload=payload,
                reply={"challenge": challenge},
                account_id=str(stored.account_id),
            )
        return VerifiedDelivery(
            delivery=delivery, payload=payload, account_id=str(stored.account_id)
        )

    async def observe(self, verified: VerifiedDelivery) -> None:
        """When it last spoke, for the person reading the schedule. Never
        raises: the event has happened whether or not this is written."""
        subscription_id = _subscription_named(verified.delivery)
        if subscription_id is None:
            return
        _, heard = self._subscriptions()
        try:
            await heard(subscription_id)
        except SQLAlchemyError:
            logger.warning(
                "connectors.webhook_sources.mcp.heard.degraded",
                subscription_id=str(subscription_id),
            )

    def normalize(self, verified: VerifiedDelivery) -> NormalizedWebhook | None:
        payload = verified.payload
        subscription_id = _subscription_named(verified.delivery)
        event_id = payload.get("eventId")
        name = payload.get("name")
        # `gap` and `terminated` are the draft's notices about the stream
        # itself; neither is something that happened, so neither runs work.
        if (
            subscription_id is None
            or payload.get("type") in ("gap", "terminated")
            or not isinstance(event_id, str)
            or not event_id
            or not isinstance(name, str)
        ):
            return None
        return NormalizedWebhook(
            payload={
                "event": name,
                "event_id": event_id,
                "timestamp": payload.get("timestamp"),
                "data": payload.get("data"),
            },
            # The server's `eventId` is its own and unbounded; the run ledger's
            # key is not. A digest keeps every id the same length, so a long one
            # cannot fail the insert that makes a redelivery run nothing.
            source_event_id=(
                f"{MCP_WEBHOOK_SOURCE}:{subscription_id}:"
                + hashlib.sha256(event_id.encode()).hexdigest()
            ),
            match={"provider_trigger_id": str(subscription_id)},
            account_id=verified.account_id,
        )
