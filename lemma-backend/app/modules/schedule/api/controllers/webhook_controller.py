"""Webhook API controller for handling external webhooks."""

from __future__ import annotations
from typing import Dict, Any

from fastapi import APIRouter, Request, HTTPException, status, Response
from app.core.log.log import get_logger

from app.modules.schedule.api.dependencies import (
    WebhookHandlerDep,
    ComposioWebhookVerifierDep,
)
from app.core.domain.events import RawWebhookReceivedEvent
from app.core.infrastructure.events.inbox import stable_event_id
from app.core.infrastructure.events.publisher import EventPublisher
from app.core.redaction import redact_value

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


def _normalize_composio_payload(verification_result: Dict[str, Any]) -> Dict[str, Any]:
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


@router.post(
    "/{source}",
    operation_id="webhook.handle",
    summary="Handle Webhook",
    description="Receive webhooks from various sources (slack, composio, jira, email, etc.)",
    status_code=status.HTTP_200_OK,
)
async def handle_webhook(
    source: str,
    request: Request,
    webhook_handler: WebhookHandlerDep,
    composio_webhook_verifier: ComposioWebhookVerifierDep,
) -> Dict[str, Any]:
    """Handle webhook from a source.

    Supports:
    - slack: Slack Events API webhooks
    - composio: Composio webhooks (requires signature verification)
    - jira: Jira webhooks
    - email: Email webhooks
    - Other sources: Generic webhook handling
    """
    headers = dict(request.headers)

    # Handle Composio webhook signature verification
    if source == "composio":
        payload_text = (await request.body()).decode("utf-8", errors="replace")

        # Verify webhook signature
        try:
            verification_result = composio_webhook_verifier.verify(
                payload_text, headers
            )
            normalized_payload = _normalize_composio_payload(verification_result)
            payload = (
                normalized_payload
                if isinstance(normalized_payload, dict)
                else {"data": verification_result.get("raw_payload")}
            )
        except Exception as exc:
            logger.debug(
                'schedule.webhook_controller.verify_composio_webhook.diagnostic',
                error_type=type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid webhook signature",
            )
    else:
        # SECURITY (interim): every source other than `composio` is unauthenticated
        # here — the request body is attacker-controllable and flows straight into
        # schedule matching + the started run's trigger context. Composio is the
        # only source with real signature verification, so reject everything else
        # until per-account verified webhook routing lands (see plan Part D). This
        # deliberately disables the legacy shared Slack/generic ingress path.
        logger.warning(
            "schedule.webhook_controller.rejecting_unauthenticated_webhook_source_s.degraded",
            source=source,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unsupported or unverified webhook source",
        )

    # Handle Slack URL verification challenge
    if source == "slack" and payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    # Publish raw webhook event for other modules (e.g. assistant surfaces) to listen to
    source_event_id = payload.get("id") or payload.get("metadata", {}).get("log_id")
    event = RawWebhookReceivedEvent(
        event_id=stable_event_id(
            {"event_id": f"schedule-webhook:{source}:{source_event_id}"}
        ),
        source=source,
        payload=payload,
        headers=redact_value(headers),
    )
    await EventPublisher.publish(event.stream_name(), event)

    # Handle webhook
    await webhook_handler.handle_webhook(
        source=source, payload=payload, headers=headers
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
