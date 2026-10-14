from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID
from pydantic import JsonValue
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from app.core.config import reveal_secret
from app.core.api.callback_page import (
    message_html,
    render_callback_page,
    safe_provider_error,
)
from app.modules.agent_surfaces.services.slack_manager import (
    ManagerSync,
    handle_sync as handle_manager_sync,
    is_manager_delivery,
)
from app.modules.agent_surfaces.config import (
    surface_settings,
)
from app.core.webhooks.signatures import constant_time_equals
from app.core.infrastructure.events.inbox import stable_event_id
from app.core.infrastructure.events.publisher import EventPublisher
from app.core.api.dependencies import get_uow_factory
from app.modules.agent_surfaces.api.controllers.webhook_seams import (
    PooledNumberLookup,
    SurfaceEventPublish,
    get_pooled_number_lookup,
    get_surface_event_publish,
)
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
    _verify_inbound_request,
    parse_webhook_platform,
    slack_url_verification,
)
from app.modules.agent_surfaces.api.controllers.webhook_verification import (
    webhook_verification_response,
)
from app.modules.agent_surfaces.api.controllers.webhook_rejections import (
    record_whatsapp_number_mismatch,
    record_whatsapp_signature_rejected,
)
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.events import SurfaceWebhookReceivedEvent
from app.modules.agent_surfaces.domain.source_event_ids import webhook_source_event_id
from app.modules.agent_surfaces.services import teams_consent
from app.modules.agent_surfaces.services.onboarding_slack_modal import (
    open_onboarding_modal,
)
from app.modules.agent_surfaces.services.surface_service import (
    AgentSurfaceService,
)
from app.modules.agent_surfaces.services.telegram_manager_service import (
    TelegramManagedBotProvisioningInProgressError,
)
from app.modules.agent_surfaces.services.webhook_security_service import (
    SurfaceWebhookAuthenticationError,
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
    expected = str(
        reveal_secret(surface_settings.telegram_manager_webhook_secret) or ""
    ).strip()
    provided = str(request.headers.get("x-telegram-bot-api-secret-token") or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Telegram manager webhook is not configured",
        )
    if not constant_time_equals(provided, expected):
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
    resolved = parse_webhook_platform(platform)
    source = resolved.value.lower()
    headers = dict(request.headers)
    raw_body = await request.body()
    payload = _decode_webhook_payload(raw_body, headers)

    # Resend inbound: a catch-all address webhook. Resolve the destination
    # address to a concrete surface and feed the normal surface-level pipeline.
    if resolved is SurfacePlatform.RESEND:
        return await _handle_resend_webhook(
            payload=payload,
            headers=headers,
            raw_body=raw_body,
            security_service=security_service,
            uow_factory=uow_factory,
        )

    # Slack sends url_verification before any signing secret is configured.
    if resolved is SurfacePlatform.SLACK and payload.get("type") == "url_verification":
        return slack_url_verification(payload, headers, security_service)

    # Authenticity failures raise SurfaceWebhookAuthenticationError (a DomainError),
    # translated to the right status by the global handler.
    security_service.assert_platform_request_allowed(resolved.value)
    receiver_surface_ids = await _verify_inbound_request(
        platform=source,
        headers=headers,
        raw_body=raw_body,
        payload=payload,
        security_service=security_service,
        uow_factory=uow_factory,
    )

    if resolved is SurfacePlatform.WHATSAPP and await _published_whatsapp_verification(
        payload, uow_factory
    ):
        return {"message": "Verification message received"}

    manager = None
    if resolved is SurfacePlatform.SLACK:
        answered, manager = await _answered_by_slack_fast_lane(
            payload, headers, receiver_surface_ids, uow_factory
        )
        if answered is not None:
            return answered

    source_event_id = webhook_source_event_id(
        source, payload, raw_body, receiver=SHARED_PLATFORM_RECEIVER
    )
    event = SurfaceWebhookReceivedEvent(
        event_id=stable_event_id({"event_id": source_event_id}),
        source=source,
        payload=payload,
        headers=_redacted_headers(headers),
        source_event_id=source_event_id,
        receiver_surface_ids=receiver_surface_ids,
    )
    await EventPublisher.publish(event.stream_name(), event)
    if manager is not None and manager.body:
        return JSONResponse(manager.body)

    # A Slack modal submission is the one webhook whose *body* is protocol, not
    # acknowledgement: Slack parses it as a response_action and shows the user
    # "We had some trouble connecting" for anything it doesn't recognise. An
    # empty 200 means "accepted, close the modal".
    if resolved is SurfacePlatform.SLACK and payload.get("type") == "view_submission":
        return Response(status_code=200)

    return {"message": "Webhook received"}


#: One pooled WhatsApp number's own callback URL.
#:
#: Meta lets a webhook be overridden per phone number, set purely by API --
#: ``POST /{PHONE_NUMBER_ID}`` with an ``override_callback_uri`` and a
#: ``verify_token`` -- and resolves it phone number, then WABA, then app
#: default. So a number that carries an override never reaches
#: ``/surfaces/webhooks/whatsapp``, and the path is what says which number a
#: delivery is for.
#:
#: The path carries ``phone_number_id``, the opaque Graph identifier, and never
#: the display number: Meta normalises a literal ``+`` in a URL path to a space,
#: so an E.164 number in a path is one that sometimes arrives mangled.
_WHATSAPP_NUMBER_WEBHOOK = "/webhooks/whatsapp/numbers/{phone_number_id}"


def _addressed_phone_number_ids(payload: Mapping[str, object]) -> set[str]:
    """Every ``metadata.phone_number_id`` a WhatsApp body claims to be for.

    A set rather than one value because one delivery may batch several changes;
    Meta only ever batches changes for one number, but nothing in the payload
    shape promises that, and a check that assumes it would pass a body it should
    have questioned.
    """
    addressed: set[str] = set()
    entries = payload.get("entry")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        for change in changes if isinstance(changes, list) else []:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            metadata = value.get("metadata") if isinstance(value, dict) else None
            identifier = (
                metadata.get("phone_number_id") if isinstance(metadata, dict) else None
            )
            if identifier:
                addressed.add(str(identifier))
    return addressed


@router.post(
    _WHATSAPP_NUMBER_WEBHOOK,
    operation_id="surface.webhook.handle_whatsapp_number",
    summary="Handle a webhook delivered to one pooled WhatsApp number",
)
async def handle_whatsapp_number_webhook(
    phone_number_id: str,
    request: Request,
    security_service: SurfaceWebhookSecurityServiceDep,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
    pooled_number: PooledNumberLookup = Depends(get_pooled_number_lookup),
    publish: SurfaceEventPublish = Depends(get_surface_event_publish),
):
    """Handle a delivery to one pooled WhatsApp number's own callback URL."""
    # Same shape as `handle_platform_webhook`: no request-scoped session, one
    # short scope for the pool lookup, nothing held across the publish.
    headers = dict(request.headers)
    raw_body = await request.body()

    # The order below is the whole point of this route, and it looks odd enough
    # to be worth stating. The body names a `metadata.phone_number_id`, and
    # selecting the verifying secret with it would be the obvious thing to
    # do -- and it would be trust before verify: those are unauthenticated
    # bytes, so a forger would name whichever number's app secret he holds and
    # have his forgery checked against exactly that one. The URL is not a claim
    # in the same sense. Each number's callback path is one this deployment
    # configured with Meta, so it is a fact about the route rather than
    # something the sender chose. Select by path, verify the HMAC over the raw
    # bytes, and only then parse.
    number = await pooled_number(phone_number_id)
    app_secret = (number.app_secret if number else None) or reveal_secret(
        surface_settings.whatsapp_app_secret
    )
    # Raises SurfaceWebhookAuthenticationError (a DomainError) on a bad or
    # missing signature, translated to the right status by the global handler.
    try:
        security_service.verify_whatsapp_app_secret(
            headers=headers,
            raw_body=raw_body,
            app_secret=app_secret,
        )
    except SurfaceWebhookAuthenticationError:
        record_whatsapp_signature_rejected(
            phone_number_id=phone_number_id,
            number=number,
            headers=headers,
            app_secret=app_secret,
        )
        raise

    payload = _decode_webhook_payload(raw_body, headers)

    # Authentic bytes, so the body may now be believed -- but only about itself.
    # `app_secret` is per Meta *app*, so numbers co-tenanted under one app share
    # it and a signature that verifies here is equally valid for any of them.
    # Without this the number in the path and the number in the body could
    # disagree and the delivery would be attributed to whichever one the path
    # happened to say.
    #
    # Ordinary set equality and not `compare_digest`: a phone number id is an
    # identifier Meta publishes, not a secret, so there is nothing here for a
    # timing oracle to leak.
    addressed = _addressed_phone_number_ids(payload)
    # Equality and not membership. This URL is a per-number override, so what
    # Meta sends to it is that number's traffic and nothing else; a body naming
    # this number *and* another is as much a body this route cannot account for
    # as one naming only another, and "at least one matched" would wave it
    # through with the rest unexamined.
    #
    # A body that names no number at all is left alone rather than rejected:
    # not every WhatsApp change carries `metadata` (account and template
    # notifications do not), and refusing those would break them for a check
    # they cannot answer. The signature already established who sent them.
    if addressed and addressed != {phone_number_id}:
        record_whatsapp_number_mismatch(
            phone_number_id=phone_number_id, addressed=addressed
        )
        raise HTTPException(
            status_code=400,
            detail="Webhook payload is addressed to a different phone number",
        )

    if await _published_whatsapp_verification(payload, uow_factory):
        return {"message": "Verification message received"}

    # The number is the receiver, not `SHARED_PLATFORM_RECEIVER`: this URL has
    # one per pooled number, and the content-hash fallback in
    # `webhook_source_event_id` is only unique per receiver.
    source_event_id = webhook_source_event_id(
        "whatsapp", payload, raw_body, receiver=phone_number_id
    )
    event = SurfaceWebhookReceivedEvent(
        event_id=stable_event_id({"event_id": source_event_id}),
        source="whatsapp",
        payload=payload,
        headers=_redacted_headers(headers),
        source_event_id=source_event_id,
    )
    await publish(event)

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
    source_event_id = webhook_source_event_id(
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
    return webhook_verification_response(
        platform.lower(),
        dict(request.query_params),
        whatsapp_verify_token=reveal_secret(surface_settings.whatsapp_verify_token),
    )


@router.get(
    _WHATSAPP_NUMBER_WEBHOOK,
    operation_id="surface.webhook.verify_whatsapp_number",
    summary="Verify a pooled WhatsApp number's own callback URL",
)
async def verify_whatsapp_number_webhook(
    phone_number_id: str,
    request: Request,
    pooled_number: PooledNumberLookup = Depends(get_pooled_number_lookup),
) -> Response:
    """Webhook verification endpoint for one pooled WhatsApp number."""
    # The handshake carries `hub.mode`, `hub.challenge` and `hub.verify_token`
    # and nothing else -- no number, no WABA, no app. So on the one shared
    # callback URL there is nothing to select a token *by*, which is why a
    # per-number `verify_token` was not expressible before this route existed.
    # Here the path is the identifier, and it is enough.
    number = await pooled_number(phone_number_id)
    verify_token = (number.verify_token if number else None) or reveal_secret(
        surface_settings.whatsapp_verify_token
    )
    # `token_matches` is constant-time and treats an absent expected token as
    # never matching, so a number with no stored token and a deployment with
    # none in settings refuses the handshake instead of handing out the
    # challenge to whoever asked.
    return webhook_verification_response(
        "whatsapp",
        dict(request.query_params),
        whatsapp_verify_token=verify_token,
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
        else reveal_secret(surface_settings.whatsapp_verify_token)
    )
    return webhook_verification_response(
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


async def _answered_by_slack_fast_lane(
    payload: dict[str, JsonValue],
    headers: dict[str, str],
    receiver_surface_ids: list[UUID] | None,
    uow_factory: UnitOfWorkFactory,
) -> tuple[Response | None, ManagerSync | None]:
    """What a Slack delivery needs answered inside the request, if anything.

    A trigger lives 3 seconds, so opening a modal and answering one cannot wait
    for a worker. Returns the response to send now, if this request is fully
    answered here, and the manager's result when the delivery was Lemma's own
    app managing the workspace -- none of the teammate lanes apply to it.
    """
    if is_manager_delivery(payload, receiver_surface_ids):
        manager = await handle_manager_sync(payload, uow_factory)
        if manager.publish:
            return None, manager
        body = JSONResponse(manager.body) if manager.body else Response(status_code=200)
        return body, manager
    if await open_onboarding_modal(payload, receiver_surface_ids, uow_factory):
        return Response(status_code=200), None
    if await _handled_slack_modal(payload, headers, receiver_surface_ids, uow_factory):
        return Response(status_code=200), None
    return None, None
