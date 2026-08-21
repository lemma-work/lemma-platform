"""Turning a raw platform webhook into an event, before any route sees it.

Signature checks, payload decoding, and the per-platform special cases that have
to happen inside the HTTP request: a Resend catch-all address resolved to a
surface, a WhatsApp verification code that is identity traffic rather than a
message, and a Slack modal whose ``trigger_id`` expires before a worker could
ever reach it.
"""

from __future__ import annotations

import json
import hashlib
from typing import Any
from uuid import UUID
from fastapi import APIRouter

from app.modules.agent_surfaces.config import surface_settings
from app.core.infrastructure.events.inbox import stable_event_id
from app.core.infrastructure.events.publisher import EventPublisher
from app.core.redaction import redact_value
from app.core.authorization.scope import uow_scope
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.api.dependencies import (
    SurfaceWebhookSecurityServiceDep,
    get_surface_service,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.events import SurfaceWebhookReceivedEvent
from app.modules.identity.domain.events import WhatsAppMobileVerificationReceivedEvent
from app.modules.identity.services.whatsapp_mobile_verification import (
    is_whatsapp_verification_configured,
    parse_reserved_verification_message,
)
from app.modules.agent_surfaces.platforms.resend.inbound import (
    normalize_resend_inbound as _normalize_resend_inbound,
)
from app.modules.agent_surfaces.api.controllers.slack_webhook_verification import (
    slack_api_app_id,
    slack_candidates_for_workspace,
    slack_team_id,
)

router = APIRouter(prefix="/surfaces", tags=["Agent Surfaces (Ingress)"])


_MODAL_OPENING_ACTION_IDS = {
    "lemma_channel_setup",
    "lemma_dm_agent_setup",
}
# App Home taps that must feel instant. Starter prompts carry no trigger_id but
# a visible lag on a "try this" button reads as a dead button.
_FAST_LANE_ACTION_PREFIXES = ("lemma_agent_dm",)


def _opens_a_slack_modal(payload: dict) -> bool:
    """True for a click whose only job is to open a modal within 3 seconds."""
    inner = payload
    for key in ("payload", "data"):
        nested = inner.get(key)
        if isinstance(nested, dict):
            inner = nested
            break
    if inner.get("type") != "block_actions":
        return False
    return any(
        isinstance(action, dict)
        and (
            action.get("action_id") in _MODAL_OPENING_ACTION_IDS
            or str(action.get("action_id") or "").startswith(_FAST_LANE_ACTION_PREFIXES)
        )
        for action in inner.get("actions") or []
    )


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


async def _slack_candidates(uow_factory: UnitOfWorkFactory, payload: dict):
    """Read the signing-secret candidates for a workspace and let the session go.

    The scope closes before `verify_slack_request` runs, so the HMAC comparison
    (and the request it authenticates) holds no pooled connection.
    """
    async with uow_scope(uow_factory) as uow:
        return await slack_candidates_for_workspace(
            service=get_surface_service(uow), team_id=slack_team_id(payload)
        )


async def _verify_inbound_request(
    *,
    platform: str,
    headers: dict[str, str],
    raw_body: bytes,
    payload: dict,
    security_service,
    uow_factory: UnitOfWorkFactory,
) -> list[UUID] | None:
    """Check a shared-endpoint request is really from the platform.

    Slack is the one platform where the answer depends on *who* sent it: an org
    running its own Slack app signs with its own secret. So the workspace has
    to be read out of the body before the signature is checked. That is safe —
    the only thing read is the team id, and the only thing it selects is which
    secret to try. Nothing acts on the payload, and a request matching no
    candidate is rejected exactly as an unsigned one would be.

    This is what lets an org's own app deliver to the shared endpoint like
    everyone else, rather than needing a URL of its own.
    """
    if platform == "slack":
        verified = security_service.verify_slack_request(
            headers=headers,
            raw_body=raw_body,
            api_app_id=slack_api_app_id(payload),
            candidates=await _slack_candidates(uow_factory, payload),
        )
        return list(verified.receiver_surface_ids) or None
    await security_service.verify_platform_request(
        platform=platform,
        headers=headers,
        raw_body=raw_body,
    )
    return None


async def _surface_for_recipients(
    normalized: dict[str, Any],
    recipients: list[Any],
    uow_factory: UnitOfWorkFactory,
) -> Any:
    """The surface this mail was delivered for, stamping the address that matched.

    Every address it was delivered for is tried, not just the one the sender
    typed: under aliasing or forwarding the pod's address is in ``received_for``
    and matching on ``to`` alone loses the mail.
    """
    async with uow_scope(uow_factory) as uow:
        repository = get_surface_service(uow).surface_repository
        for address in recipients:
            surface = await repository.get_active_by_address(
                platform="RESEND", address=address
            )
            if surface is not None:
                normalized["to"] = address
                return surface
    return None


async def _handle_resend_webhook(
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
    raw_body: bytes,
    security_service: SurfaceWebhookSecurityServiceDep,
    uow_factory: UnitOfWorkFactory,
) -> dict[str, str]:
    """Resolve a catch-all Resend delivery to one surface, then publish it."""
    # Authenticate the Svix signature over the raw body BEFORE trusting any
    # of it (Resend does not go through assert_platform_request_allowed).
    await security_service.verify_resend_request(headers=headers, raw_body=raw_body)
    normalized = _normalize_resend_inbound(payload)
    recipients = normalized.get("recipients") or []
    if not recipients:
        return {"message": "Ignored: no destination address"}

    surface = await _surface_for_recipients(normalized, recipients, uow_factory)
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


async def _published_whatsapp_verification(payload: dict[str, Any]) -> bool:
    """Publish a reserved verification message, when that is what arrived.

    Verification commands are identity traffic, not agent messages. This is
    reached only after Meta's raw-body signature succeeds, and only acts when
    the message targets Lemma's configured global phone-number id.
    """
    verification = parse_reserved_verification_message(payload)
    if verification is None or not is_whatsapp_verification_configured():
        return False
    code, sender_wa_id, destination_id, message_id = verification
    if destination_id != surface_settings.whatsapp_phone_number_id:
        return False

    identity_event = WhatsAppMobileVerificationReceivedEvent(
        event_id=stable_event_id(
            {"whatsapp_mobile_verification_message_id": message_id}
        ),
        code=code,
        sender_wa_id=sender_wa_id,
        destination_phone_number_id=destination_id,
        whatsapp_message_id=message_id,
    )
    await EventPublisher.publish(identity_event.stream_name(), identity_event)
    return True


async def _handled_slack_modal(
    payload: dict[str, Any],
    headers: dict[str, str],
    receiver_surface_ids: Any,
    uow_factory: UnitOfWorkFactory,
) -> bool:
    """Open a Slack modal inline, because ``trigger_id`` cannot wait for a worker.

    It dies ~3 seconds after the click, and by the time a worker dequeues the
    event it is usually already expired -- so this runs in the HTTP request,
    before anything is published.
    """
    if not _opens_a_slack_modal(payload):
        return False
    from app.modules.agent_surfaces.events.handlers import (
        build_surface_event_handler,
    )

    async with uow_factory() as uow:
        return await build_surface_event_handler(uow).try_handle_channel_setup(
            SurfacePlatformWebhookIngress(
                source="slack",
                payload=payload,
                headers=_redacted_headers(headers),
                receiver_surface_ids=receiver_surface_ids,
            )
        )
