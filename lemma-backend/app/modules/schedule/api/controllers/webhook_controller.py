"""Webhook API controller for handling external webhooks."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.core.log.log import get_logger
from app.modules.schedule.api.dependencies import (
    WebhookHandlerDep,
    WebhookSourceRegistryDep,
)
from app.modules.schedule.domain.webhook_source import (
    WebhookDelivery,
    WebhookNotVerified,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

# A delivery larger than this is refused before it is read. Nothing legitimate
# comes close -- GitHub caps its own payloads at 25 MB and the largest real one
# is a `push` with a long `commits[]` -- and without a cap the body flows into
# schedule matching, `schedule_runs`, the outbox and Redis on a path whose rate
# an unauthenticated sender chooses.
MAX_WEBHOOK_BODY_BYTES = 1_048_576


@router.post(
    "/{source}",
    operation_id="webhook.handle",
    summary="Handle Webhook",
    description="Receive a webhook from a verified source.",
    status_code=status.HTTP_200_OK,
)
async def handle_webhook(
    source: str,
    request: Request,
    webhook_handler: WebhookHandlerDep,
    sources: WebhookSourceRegistryDep,
) -> Dict[str, Any]:
    """Verify an inbound delivery, normalize it, and let it match schedules.

    `source` comes from the URL, so the sender chooses it. The registry is the
    allow-list: a source with no plugin is refused here and never reaches
    matching, a run, or an agent's first message.
    """
    plugin = sources.for_source(source)
    if plugin is None:
        logger.warning(
            "schedule.webhook_controller.rejecting_unknown_webhook_source_s.degraded",
            source=source,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unsupported or unverified webhook source",
        )

    raw_body = await request.body()
    if len(raw_body) > MAX_WEBHOOK_BODY_BYTES:
        logger.warning(
            "schedule.webhook_controller.rejecting_oversized_delivery.degraded",
            source=source,
            size=len(raw_body),
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Webhook payload is too large",
        )

    delivery = WebhookDelivery(
        source=source, raw_body=raw_body, headers=dict(request.headers)
    )
    try:
        verified = await plugin.verify(delivery)
    except WebhookNotVerified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook signature",
        )

    # State the delivery changes about the source itself -- an App uninstalled,
    # repositories removed from one. Never allowed to fail the delivery: it has
    # already happened, and a non-2xx only makes the provider send it again.
    try:
        await plugin.observe(verified)
    except Exception:
        logger.warning(
            "schedule.webhook_controller.source_observation_failed.degraded",
            source=source,
            exc_info=True,
        )

    normalized = plugin.normalize(verified)
    if normalized is None:
        # An event nothing is subscribed to. Answered 2xx on purpose: a provider
        # that collects non-2xx responses retries them and then disables the
        # hook, so a shrug has to look like success.
        return {"message": "Webhook received"}

    # No fan-out here. This used to publish a `RawWebhookReceivedEvent` on
    # `webhook_events` "for other modules to listen to" and nothing ever did --
    # no module declares a consumer group on that stream -- so every delivery
    # paid an outbox insert, a Redis XADD and a header redaction for a message
    # nobody read. Surfaces have their own verified ingress.
    await webhook_handler.handle_webhook(
        source=source,
        payload=normalized.payload,
        headers=dict(request.headers),
        normalized=normalized,
    )
    return {
        "message": "Webhook received",
    }


@router.get(
    "/{source}/verify",
    operation_id="webhook.verify",
    summary="Verify Webhook",
    description="Webhook verification endpoint for platforms that require it",
)
async def verify_webhook(
    source: str,
    request: Request,
) -> Response:
    """Verify webhook (for platforms like WhatsApp, etc.)."""
    params = request.query_params

    if source == "whatsapp":
        mode = params.get("hub.mode")
        challenge = params.get("hub.challenge")

        if mode == "subscribe" and challenge:
            logger.debug(
                "schedule.webhook_controller.verified_whatsapp_webhook.observed"
            )
            return Response(content=challenge, media_type="text/plain")

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Verification failed"
    )
