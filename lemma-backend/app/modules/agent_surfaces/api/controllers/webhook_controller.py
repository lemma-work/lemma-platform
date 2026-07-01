from __future__ import annotations

import json
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from app.modules.agent_surfaces.config import surface_settings
from app.core.infrastructure.events.message_bus import get_message_bus
from app.modules.agent_surfaces.api.dependencies import (
    SurfaceWebhookSecurityServiceDep,
    get_surface_service,
)
from app.modules.agent_surfaces.domain.events import SurfaceWebhookReceivedEvent
from app.modules.agent_surfaces.infrastructure.debug.raw_webhook_file_logger import (
    log_raw_webhook_event,
)
from app.modules.agent_surfaces.services.surface_service import (
    AgentSurfaceService,
)

router = APIRouter(prefix="/surfaces", tags=["Agent Surfaces (Ingress)"])


def _decode_webhook_payload(raw_body: bytes, headers: dict[str, str]) -> dict:
    """Decode a webhook body to JSON.

    Most platforms send JSON. Slack interactivity (block_actions /
    view_submission) is ``application/x-www-form-urlencoded`` with a single
    ``payload=<json>`` field. Signature verification still runs over the raw
    bytes, so decoding here does not weaken auth.
    """
    if not raw_body:
        return {}
    content_type = headers.get("content-type") or headers.get("Content-Type") or ""
    try:
        if content_type.startswith("application/x-www-form-urlencoded"):
            from urllib.parse import parse_qs

            fields = parse_qs(raw_body.decode("utf-8"))
            payload_values = fields.get("payload")
            return json.loads(payload_values[0]) if payload_values else {}
        return json.loads(raw_body.decode("utf-8"))
    except Exception:
        return {}


@router.post(
    "/webhooks/{platform}",
    operation_id="surface.webhook.handle_platform",
    summary="Handle platform-level surface webhook",
)
async def handle_platform_webhook(
    platform: str,
    request: Request,
    security_service: SurfaceWebhookSecurityServiceDep,
    message_bus=Depends(get_message_bus),
):
    """Handle platform-level webhook callbacks."""
    headers = dict(request.headers)
    raw_body = await request.body()
    payload = _decode_webhook_payload(raw_body, headers)

    # Slack sends url_verification before any signing secret is configured — respond immediately.
    if platform == "slack" and payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    # Authenticity failures raise SurfaceWebhookAuthenticationError (a DomainError),
    # translated to the right status by the global handler.
    security_service.assert_platform_request_allowed(platform)
    await security_service.verify_platform_request(
        platform=platform,
        headers=headers,
        raw_body=raw_body,
    )

    await log_raw_webhook_event(source=platform, payload=payload, headers=headers)

    event = SurfaceWebhookReceivedEvent(
        source=platform,
        payload=payload,
        headers=headers,
    )
    await message_bus.publish(stream=event.stream_name(), event=event)

    return {"message": "Webhook received"}


@router.post(
    "/{surface_id}/webhook",
    operation_id="surface.webhook.handle_surface",
    summary="Handle surface-level webhook",
)
async def handle_surface_webhook(
    surface_id: UUID,
    request: Request,
    security_service: SurfaceWebhookSecurityServiceDep,
    service: AgentSurfaceService = Depends(get_surface_service),
    message_bus=Depends(get_message_bus),
):
    """Handle webhooks addressed to one concrete surface."""
    headers = dict(request.headers)
    raw_body = await request.body()
    payload = _decode_webhook_payload(raw_body, headers)

    # get_surface raises AgentSurfaceNotFoundError (404) and verify_surface_request
    # raises SurfaceWebhookAuthenticationError — both DomainErrors, translated by
    # the global handler.
    surface = await service.get_surface(surface_id)
    await security_service.verify_surface_request(
        surface=surface,
        headers=headers,
        raw_body=raw_body,
    )

    source = surface.surface_type.value.lower()
    await log_raw_webhook_event(source=source, payload=payload, headers=headers)

    event = SurfaceWebhookReceivedEvent(
        source=source,
        payload=payload,
        headers=headers,
        surface_id=surface.id,
    )
    await message_bus.publish(stream=event.stream_name(), event=event)

    return {"message": "Webhook received"}


def _webhook_verification_response(
    platform: str, params: dict[str, str], *, whatsapp_verify_token: str | None = None
) -> Response:
    """Shared GET-verification handshake (WhatsApp hub challenge / Telegram ok)."""
    if platform == "whatsapp":
        mode = params.get("hub.mode")
        challenge = params.get("hub.challenge")
        verify_token = params.get("hub.verify_token")

        security_enabled = bool(surface_settings.surface_webhook_security_enabled)
        if mode == "subscribe" and challenge and (
            not security_enabled or verify_token == whatsapp_verify_token
        ):
            return Response(content=challenge, media_type="text/plain")

    if platform == "telegram":
        return Response(content="ok", media_type="text/plain")

    raise HTTPException(status_code=403, detail="Verification failed")


@router.get(
    "/webhooks/{platform}",
    operation_id="surface.webhook.verify",
    summary="Verify surface webhook using the platform callback URL",
)
async def verify_surface_webhook(
    platform: str,
    request: Request,
):
    """Webhook verification endpoint for platforms that require it."""
    return _webhook_verification_response(
        platform,
        dict(request.query_params),
        whatsapp_verify_token=surface_settings.whatsapp_verify_token,
    )


@router.get(
    "/{surface_id}/webhook",
    operation_id="surface.webhook.verify_surface",
    summary="Verify surface webhook using a surface-level callback URL",
)
async def verify_direct_surface_webhook(
    surface_id: UUID,
    request: Request,
    security_service: SurfaceWebhookSecurityServiceDep,
    service: AgentSurfaceService = Depends(get_surface_service),
):
    """Webhook verification endpoint for platforms that require it.

    WhatsApp surfaces bound to a connector account are verified against that
    account's own ``verify_token`` (never the system-wide one) so each
    customer's WhatsApp Business webhook config only has to match their own
    credentials.
    """
    surface = await service.get_surface(surface_id)
    platform = surface.surface_type.value.lower()
    whatsapp_verify_token = (
        await security_service.resolve_whatsapp_verify_token(surface)
        if platform == "whatsapp"
        else surface_settings.whatsapp_verify_token
    )
    return _webhook_verification_response(
        platform,
        dict(request.query_params),
        whatsapp_verify_token=whatsapp_verify_token,
    )


@router.get(
    "/teams/admin-consent/callback",
    operation_id="agent.surface.teams_admin_consent_callback",
)
async def teams_admin_consent_callback(
    tenant: str | None = None,
    admin_consent: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> HTMLResponse:
    if error:
        html = f"""
        <html><body style="font-family:sans-serif;padding:2rem">
        <h2>&#10060; Admin consent failed</h2>
        <p><strong>{error}</strong>: {error_description or ''}</p>
        <p>Please contact your administrator or try again.</p>
        </body></html>
        """
        return HTMLResponse(content=html, status_code=400)

    if not tenant or admin_consent != "True" or not state:
        html = """
        <html><body style="font-family:sans-serif;padding:2rem">
        <h2>&#9888;&#65039; Invalid callback</h2>
        <p>Missing required parameters. Please retry the consent flow.</p>
        </body></html>
        """
        return HTMLResponse(content=html, status_code=400)

    try:
        from uuid import UUID

        surface_id = UUID(state)
    except ValueError:
        return HTMLResponse(content="<html><body>Invalid state parameter.</body></html>", status_code=400)

    surface = await service.activate_after_consent(surface_id=surface_id, tenant_id=tenant)

    if surface is None:
        html = """
        <html><body style="font-family:sans-serif;padding:2rem">
        <h2>&#9888;&#65039; Surface not found</h2>
        <p>The Teams surface could not be located. It may have been deleted.</p>
        </body></html>
        """
        return HTMLResponse(content=html, status_code=404)

    html = """
    <html><body style="font-family:sans-serif;padding:2rem;max-width:480px;margin:auto">
    <h2>&#9989; Admin consent granted</h2>
    <p>The Lemma Teams bot is now active and ready to use.</p>
    <p>You can close this window and return to the Lemma dashboard.</p>
    </body></html>
    """
    return HTMLResponse(content=html)
