from __future__ import annotations

import json
import hashlib
import hmac
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from app.core.api.callback_page import (
    message_html,
    render_callback_page,
    safe_provider_error,
)
from app.modules.agent_surfaces.config import surface_settings
from app.core.infrastructure.events.inbox import stable_event_id
from app.core.infrastructure.events.publisher import EventPublisher
from app.core.redaction import redact_value
from app.modules.agent_surfaces.api.dependencies import (
    SurfaceWebhookSecurityServiceDep,
    TelegramManagerServiceDep,
    get_surface_service,
)
from app.modules.agent_surfaces.domain.events import SurfaceWebhookReceivedEvent
from app.modules.identity.domain.events import WhatsAppMobileVerificationReceivedEvent
from app.modules.identity.services.whatsapp_mobile_verification import (
    is_whatsapp_verification_configured,
    parse_reserved_verification_message,
)
from app.modules.agent_surfaces.services import teams_consent
from app.modules.agent_surfaces.services.surface_service import (
    AgentSurfaceService,
)
from app.modules.agent_surfaces.services.telegram_manager_service import (
    TelegramManagedBotProvisioningInProgressError,
)

router = APIRouter(prefix="/surfaces", tags=["Agent Surfaces (Ingress)"])


def _surface_source_event_id(platform: str, payload: dict, raw_body: bytes) -> str:
    candidates: list[object] = [
        payload.get("event_id"),
        payload.get("update_id"),
        payload.get("id"),
        payload.get("message_id"),
        payload.get("data", {}).get("message_id")
        if isinstance(payload.get("data"), dict)
        else None,
    ]
    for candidate in candidates:
        if candidate is not None and str(candidate):
            return f"{platform}:{candidate}"
    return f"{platform}:content-sha256:{hashlib.sha256(raw_body).hexdigest()}"


def _redacted_headers(headers: dict[str, str]) -> dict[str, str]:
    value = redact_value(headers)
    return {str(key): str(item) for key, item in value.items()}


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


def _email_address(value) -> str | None:
    """Pull a bare email address out of a string / {address} / list shape."""
    if isinstance(value, list):
        return _email_address(value[0]) if value else None
    if isinstance(value, dict):
        return _email_address(value.get("address") or value.get("email"))
    if isinstance(value, str):
        text = value.strip()
        if "<" in text and ">" in text:
            text = text[text.index("<") + 1 : text.index(">")].strip()
        return text or None
    return None


def _normalize_resend_inbound(payload: dict) -> dict:
    """Normalize a Resend inbound webhook into the flat dict the parser expects.

    Tolerates both a flat body and a Svix-style ``{type, data: {...}}`` envelope,
    and address fields shaped as strings, ``{address}`` dicts, or lists.
    """
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    raw_headers = data.get("headers") or []
    header_map: dict[str, str] = {}
    if isinstance(raw_headers, list):
        for h in raw_headers:
            if isinstance(h, dict) and h.get("name"):
                header_map[str(h["name"]).lower()] = str(h.get("value") or "")
    elif isinstance(raw_headers, dict):
        header_map = {str(k).lower(): str(v) for k, v in raw_headers.items()}

    references_raw = data.get("references") or header_map.get("references") or ""
    if isinstance(references_raw, str):
        references = [r for r in references_raw.split() if r]
    else:
        references = [str(r) for r in (references_raw or [])]

    return {
        "from": _email_address(data.get("from")),
        "from_name": (data.get("from") or {}).get("name")
        if isinstance(data.get("from"), dict)
        else None,
        "to": _email_address(data.get("to")),
        "subject": data.get("subject") or header_map.get("subject"),
        "text": data.get("text"),
        "html": data.get("html"),
        "message_id": data.get("message_id") or header_map.get("message-id"),
        "in_reply_to": data.get("in_reply_to") or header_map.get("in-reply-to"),
        "references": references,
    }


@router.post(
    "/webhooks/telegram-manager",
    operation_id="surface.webhook.handle_telegram_manager",
    summary="Handle Telegram manager-bot webhook",
)
async def handle_telegram_manager_webhook(
    request: Request,
    service: TelegramManagerServiceDep,
):
    expected = str(surface_settings.telegram_manager_webhook_secret or "").strip()
    provided = str(request.headers.get("x-telegram-bot-api-secret-token") or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Telegram manager webhook is not configured",
        )
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret")
    payload = _decode_webhook_payload(await request.body(), dict(request.headers))
    try:
        await service.handle_update(payload)
    except TelegramManagedBotProvisioningInProgressError as exc:
        raise HTTPException(
            status_code=503,
            detail="Telegram managed-bot setup is still provisioning",
            headers={"Retry-After": "1"},
        ) from exc
    return {"message": "Webhook received"}


@router.post(
    "/webhooks/{platform}",
    operation_id="surface.webhook.handle_platform",
    summary="Handle platform-level surface webhook",
)
async def handle_platform_webhook(
    platform: str,
    request: Request,
    security_service: SurfaceWebhookSecurityServiceDep,
    service: AgentSurfaceService = Depends(get_surface_service),
):
    """Handle platform-level webhook callbacks."""
    headers = dict(request.headers)
    raw_body = await request.body()
    payload = _decode_webhook_payload(raw_body, headers)

    # Resend inbound: a catch-all address webhook. Resolve the destination
    # address to a concrete surface and feed the normal surface-level pipeline.
    if platform == "resend":
        # Authenticate the Svix signature over the raw body BEFORE trusting any
        # of it (Resend does not go through assert_platform_request_allowed).
        await security_service.verify_resend_request(headers=headers, raw_body=raw_body)
        normalized = _normalize_resend_inbound(payload)
        to_address = normalized.get("to")
        if not to_address:
            return {"message": "Ignored: no destination address"}
        surface = await service.surface_repository.get_active_by_address(
            platform="RESEND", address=to_address
        )
        if surface is None:
            return {"message": "Ignored: no surface for address"}
        source_event_id = _surface_source_event_id("resend", normalized, raw_body)
        event = SurfaceWebhookReceivedEvent(
            event_id=stable_event_id({"event_id": source_event_id}),
            source="resend",
            payload=normalized,
            headers=_redacted_headers(headers),
            surface_id=surface.id,
            source_event_id=source_event_id,
        )
        await EventPublisher.publish(event.stream_name(), event)
        return {"message": "Webhook received"}

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

    # Verification commands are identity traffic, not agent messages. The
    # request is intercepted only after Meta's raw-body signature succeeds and
    # only when it targets Lemma's configured global phone-number id.
    if platform == "whatsapp":
        verification = parse_reserved_verification_message(payload)
        if verification is not None and is_whatsapp_verification_configured():
            code, sender_wa_id, destination_id, message_id = verification
            if destination_id == surface_settings.whatsapp_phone_number_id:
                identity_event = WhatsAppMobileVerificationReceivedEvent(
                    event_id=stable_event_id(
                        {"whatsapp_mobile_verification_message_id": message_id}
                    ),
                    code=code,
                    sender_wa_id=sender_wa_id,
                    destination_phone_number_id=destination_id,
                    whatsapp_message_id=message_id,
                )
                await EventPublisher.publish(
                    identity_event.stream_name(), identity_event
                )
                return {"message": "Verification message received"}

    source_event_id = _surface_source_event_id(platform, payload, raw_body)
    event = SurfaceWebhookReceivedEvent(
        event_id=stable_event_id({"event_id": source_event_id}),
        source=platform,
        payload=payload,
        headers=_redacted_headers(headers),
        source_event_id=source_event_id,
    )
    await EventPublisher.publish(event.stream_name(), event)

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
    source_event_id = _surface_source_event_id(source, payload, raw_body)
    event = SurfaceWebhookReceivedEvent(
        event_id=stable_event_id({"event_id": source_event_id}),
        source=source,
        payload=payload,
        headers=_redacted_headers(headers),
        surface_id=surface.id,
        source_event_id=source_event_id,
    )
    await EventPublisher.publish(event.stream_name(), event)

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
        if (
            mode == "subscribe"
            and challenge
            and (not security_enabled or verify_token == whatsapp_verify_token)
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


_TEAMS_LABEL = "Microsoft Teams"
# Teams is a natively supported app, so it carries no catalog icon; the
# frontend ships its mark under /connector-logos.
_TEAMS_LOGO = "teams.svg"


def _consent_failed(message: str, *, status_code: int = 400) -> HTMLResponse:
    return render_callback_page(
        succeeded=False,
        app_label=_TEAMS_LABEL,
        icon=None,
        logo_asset=_TEAMS_LOGO,
        title=f"{_TEAMS_LABEL} wasn’t connected",
        body_html=message_html(message),
        status_code=status_code,
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
        # Microsoft hands these back as query parameters on a public endpoint,
        # so neither the code nor its description may be reflected as written.
        # The code is reduced to its bounded vocabulary; the description is
        # dropped entirely, since it is prose we cannot vouch for.
        return _consent_failed(
            f"Microsoft ended the consent request with "
            f"“{safe_provider_error(error)}”, so the bot was not installed. "
            "You can start the consent flow again from Lemma."
        )

    if not tenant or admin_consent != "True" or not state:
        return _consent_failed(
            "The consent request came back without the details Lemma needs to "
            "finish it. You can start the consent flow again from Lemma."
        )

    surface_id_part, _, nonce = state.partition(":")
    try:
        surface_id = UUID(surface_id_part)
    except ValueError:
        return _consent_failed(
            "The consent request came back with an identifier Lemma could not "
            "read, so nothing was changed. You can start the consent flow again "
            "from Lemma."
        )

    # This endpoint is unauthenticated and every parameter is caller-supplied,
    # so the nonce is what distinguishes a real Microsoft round-trip from a
    # direct call by anyone who saw a surface id. Spend it before touching the
    # surface: activation sets the tenant binding, and that write is first-wins.
    if not await teams_consent.consume_nonce(surface_id, nonce):
        return _consent_failed(
            "This consent link is no longer valid, so nothing was changed. You "
            "can start the consent flow again from Lemma."
        )

    surface = await service.activate_after_consent(
        surface_id=surface_id, tenant_id=tenant
    )

    if surface is None:
        return _consent_failed(
            "Lemma could not find the Teams surface this consent was for — it "
            "may have been deleted since the request was sent.",
            status_code=404,
        )

    return render_callback_page(
        succeeded=True,
        app_label=_TEAMS_LABEL,
        icon=None,
        logo_asset=_TEAMS_LOGO,
        title=f"{_TEAMS_LABEL} is connected",
        body_html=message_html(
            "The Lemma bot is installed and ready to use in your workspace."
        ),
    )
