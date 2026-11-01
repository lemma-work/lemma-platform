"""Webhook API controller for handling external webhooks."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.core.log.log import get_logger
from app.modules.schedule.api.dependencies import (
    WebhookHandlerDep,
    WebhookSourceRegistryDep,
)
from app.modules.schedule.contracts.webhook_source import (
    WebhookDelivery,
    WebhookNotVerified,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

# Most schedule sources carry small event envelopes. Keep their limit low so
# accepting GitHub's larger documented payload ceiling does not widen every
# public ingress route by 25x.
MAX_WEBHOOK_BODY_BYTES = 1_048_576
# GitHub caps webhook payloads at 25 MB. Its push payload may contain up to
# 2048 commit objects, so it needs a provider-specific ceiling.
MAX_GITHUB_WEBHOOK_BODY_BYTES = 25_000_000


async def _read_bounded_body(request: Request, *, source: str) -> bytes:
    """Read no more than the configured webhook body limit into memory.

    Content-Length is an early rejection hint, not a trusted bound: requests
    without it, or with a smaller declared length, are still capped as chunks
    arrive. This keeps the memory cost of an unauthenticated delivery bounded.
    """
    max_body_bytes = (
        MAX_GITHUB_WEBHOOK_BODY_BYTES if source == "github" else MAX_WEBHOOK_BODY_BYTES
    )
    content_length = request.headers.get("content-length")
    if content_length is not None:
        if not content_length.isascii() or not content_length.isdecimal():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid webhook content length",
            )
        normalized_length = content_length.lstrip("0") or "0"
        maximum_length = str(max_body_bytes)
        if len(normalized_length) > len(maximum_length) or (
            len(normalized_length) == len(maximum_length)
            and normalized_length > maximum_length
        ):
            logger.warning(
                "schedule.webhook_controller.rejecting_oversized_delivery.degraded",
                source=source,
                size=max_body_bytes + 1,
            )
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Webhook payload is too large",
            )

    body = bytearray()
    async for chunk in request.stream():
        observed_size = len(body) + len(chunk)
        if observed_size > max_body_bytes:
            logger.warning(
                "schedule.webhook_controller.rejecting_oversized_delivery.degraded",
                source=source,
                size=observed_size,
            )
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Webhook payload is too large",
            )
        body.extend(chunk)
    return bytes(body)


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

    raw_body = await _read_bounded_body(request, source=source)

    delivery = WebhookDelivery(
        source=source,
        raw_body=raw_body,
        headers=dict(request.headers),
        query=dict(request.query_params),
    )
    try:
        verified = await plugin.verify(delivery)
    except Exception as exc:
        # Anything at all: a plugin that raises has not verified the delivery,
        # and saying that once here is why no plugin needs its own broad catch.
        # The reason stays in the log -- told to the sender it is a hint at what
        # to change in the next attempt.
        if not isinstance(exc, WebhookNotVerified):
            logger.warning(
                "schedule.webhook_controller.source_verifier_raised.degraded",
                source=source,
                exc_info=True,
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook signature",
        )

    if verified.reply is not None:
        # A handshake the source asked to have answered, not an event.
        return verified.reply

    # State the delivery changes about the source itself -- an App uninstalled,
    # repositories removed from one. Deliberately not wrapped: retirement is
    # idempotent, so letting a transient database failure surface as a 500 gets
    # the delivery retried and applied, where swallowing it would leave the
    # accounts and schedules standing with nothing to notice. For every other
    # event this returns without touching anything and cannot fail.
    await plugin.observe(verified)

    normalized = plugin.normalize(verified)
    if normalized is None:
        # An event nothing is subscribed to. Answered 2xx on purpose: a provider
        # that collects non-2xx responses retries them and then disables the
        # hook, so a shrug has to look like success.
        return {"message": "Webhook received"}

    # No fan-out here. This used to publish a raw-webhook event on
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
