from __future__ import annotations

import hmac
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from app.core.api.callback_page import (
    message_html,
    render_callback_page,
    safe_provider_error,
)
from app.modules.agent_surfaces.config import (
    surface_settings,
    surface_webhook_verification_enabled,
)
from app.core.infrastructure.events.inbox import stable_event_id
from app.core.infrastructure.events.publisher import EventPublisher
from app.core.api.dependencies import get_uow_factory
from app.core.authorization.scope import uow_scope
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.api.dependencies import (
    SurfaceWebhookSecurityServiceDep,
    TelegramManagerServiceDep,
    get_surface_service,
)
from app.modules.agent_surfaces.api.controllers.webhook_ingest import (
    SHARED_PLATFORM_RECEIVER,
    _decode_webhook_payload,
    _handle_resend_webhook,
    _handled_slack_modal,
    _published_whatsapp_verification,
    _redacted_headers,
    _surface_source_event_id,
    _verify_inbound_request,
)
from app.modules.agent_surfaces.domain.events import SurfaceWebhookReceivedEvent
from app.modules.agent_surfaces.services import teams_consent
from app.modules.agent_surfaces.services.surface_service import (
    AgentSurfaceService,
)
from app.modules.agent_surfaces.services.telegram_manager_service import (
    TelegramManagedBotProvisioningInProgressError,
)

router = APIRouter(prefix="/surfaces", tags=["Agent Surfaces (Ingress)"])


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
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
):
    """Handle platform-level webhook callbacks."""
    # No request-scoped session on this route by design. It is an inbound
    # webhook endpoint -- the request rate belongs to the sending platform --
    # and it publishes to Redis up to three times. Every lookup below opens its
    # own short scope, so nothing is held across a publish or a signature check.
    # Deliberately a comment, not part of the docstring: the docstring becomes
    # the endpoint's public OpenAPI description.
    headers = dict(request.headers)
    raw_body = await request.body()
    payload = _decode_webhook_payload(raw_body, headers)

    # Resend inbound: a catch-all address webhook. Resolve the destination
    # address to a concrete surface and feed the normal surface-level pipeline.
    if platform == "resend":
        return await _handle_resend_webhook(
            payload=payload,
            headers=headers,
            raw_body=raw_body,
            security_service=security_service,
            uow_factory=uow_factory,
        )

    # Slack sends url_verification before any signing secret is configured — respond immediately.
    if platform == "slack" and payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    # Authenticity failures raise SurfaceWebhookAuthenticationError (a DomainError),
    # translated to the right status by the global handler.
    security_service.assert_platform_request_allowed(platform)
    receiver_surface_ids = await _verify_inbound_request(
        platform=platform,
        headers=headers,
        raw_body=raw_body,
        payload=payload,
        security_service=security_service,
        uow_factory=uow_factory,
    )

    if platform == "whatsapp" and await _published_whatsapp_verification(payload):
        return {"message": "Verification message received"}

    if platform == "slack" and await _handled_slack_modal(
        payload, headers, receiver_surface_ids, uow_factory
    ):
        return Response(status_code=200)

    source_event_id = _surface_source_event_id(
        platform, payload, raw_body, receiver=SHARED_PLATFORM_RECEIVER
    )
    event = SurfaceWebhookReceivedEvent(
        event_id=stable_event_id({"event_id": source_event_id}),
        source=platform,
        payload=payload,
        headers=_redacted_headers(headers),
        source_event_id=source_event_id,
        receiver_surface_ids=receiver_surface_ids,
    )
    await EventPublisher.publish(event.stream_name(), event)

    # A Slack modal submission is the one webhook whose *body* is protocol, not
    # acknowledgement: Slack parses it as a response_action and shows the user
    # "We had some trouble connecting" for anything it doesn't recognise. An
    # empty 200 means "accepted, close the modal".
    if platform == "slack" and payload.get("type") == "view_submission":
        return Response(status_code=200)

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
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
):
    """Handle webhooks addressed to one concrete surface."""
    # Same shape as `handle_platform_webhook`: no request-scoped session, one
    # short scope for the surface lookup, nothing held across the publish.
    headers = dict(request.headers)
    raw_body = await request.body()
    payload = _decode_webhook_payload(raw_body, headers)

    # get_surface raises AgentSurfaceNotFoundError (404) and verify_surface_request
    # raises SurfaceWebhookAuthenticationError — both DomainErrors, translated by
    # the global handler.
    async with uow_scope(uow_factory) as uow:
        surface = await get_surface_service(uow).get_surface(surface_id)
    await security_service.verify_surface_request(
        surface=surface,
        headers=headers,
        raw_body=raw_body,
    )

    source = surface.surface_type.value.lower()
    # Named by the surface, not just the platform: a Telegram ``update_id`` is a
    # per-bot counter, so every bot's first update is 1 and two of them would
    # otherwise share one inbox row.
    source_event_id = _surface_source_event_id(
        source, payload, raw_body, receiver=str(surface.id)
    )
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

        # The same answer the POST paths get, rather than the raw flag: the
        # switch is honoured on a developer machine and nowhere else.
        security_enabled = surface_webhook_verification_enabled()
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
