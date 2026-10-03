"""Turning a raw platform webhook into an event, before any route sees it.

Signature checks, payload decoding, and the per-platform special cases that have
to happen inside the HTTP request: a Resend catch-all address resolved to a
surface, a WhatsApp verification code that is identity traffic rather than a
message, and a Slack modal whose ``trigger_id`` expires before a worker could
ever reach it.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from pydantic import JsonValue

from app.modules.agent_surfaces.contracts.whatsapp import (
    deployment_owns_whatsapp_number,
)
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
from app.modules.identity.contracts.whatsapp_verification import (
    is_whatsapp_verification_configured,
    parse_reserved_verification_message,
)
from app.modules.agent_surfaces.platforms.resend.inbound import (
    normalize_resend_inbound as _normalize_resend_inbound,
)
from app.modules.agent_surfaces.domain.source_event_ids import (
    resend_webhook_source_event_id,
)
from app.modules.agent_surfaces.services.resend_recipients import (
    surface_for_recipients,
)
from app.modules.agent_surfaces.api.controllers.slack_webhook_verification import (
    slack_api_app_id,
    slack_candidates_for_workspace,
    slack_team_id,
)

_MODAL_OPENING_ACTION_IDS = {
    "lemma_channel_setup",
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


#: The receiver a delivery to the shared ``/surfaces/webhooks/{platform}``
#: endpoint arrived on. That URL has one receiver per deployment -- it is
#: verified against the deployment's own credential for the platform -- so the
#: constant names it honestly. (Slack also serves an org's own app there, and
#: needs no help: its ``event_id`` is unique across workspaces.)
SHARED_PLATFORM_RECEIVER = "shared"


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

    async with uow_scope(uow_factory) as uow:
        surface = await surface_for_recipients(
            get_surface_service(uow).surface_repository, normalized, recipients
        )
    if surface is None:
        return {"message": "Ignored: no surface for address"}

    # Resend's own id rather than the generic candidate list, because the
    # polling receiver has to be able to mint the same one for the same email
    # and has no raw body to fall back on.
    source_event_id = resend_webhook_source_event_id(
        normalized, raw_body, receiver=str(surface.id)
    )
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


async def _published_whatsapp_verification(
    payload: dict[str, Any], uow_factory: UnitOfWorkFactory
) -> bool:
    """Publish a reserved verification message, when that is what arrived.

    Verification commands are identity traffic, not agent messages. This is
    reached only after Meta's raw-body signature succeeds, and only acts when
    the message targets a number this deployment owns.

    "Owns" used to mean the one in settings, because there was one. Every
    pooled number is a system number and behaves the same, so a person who sends
    their code to whichever of our numbers they happen to be talking to gets
    verified -- telling them "wrong number, use the other one" would be a
    distinction only we can see. The pool is consulted only when the arriving
    number is not the configured one, so a single-number deployment does no
    extra work.
    """
    verification = parse_reserved_verification_message(payload)
    if verification is None or not await is_whatsapp_verification_configured():
        return False
    code, sender_wa_id, destination_id, message_id = verification
    if not await deployment_owns_whatsapp_number(destination_id):
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


async def _without_whatsapp_verifications(
    payload: dict[str, JsonValue],
    uow_factory: UnitOfWorkFactory,
    *,
    publish: VerificationPublisher | None = None,
) -> dict[str, JsonValue] | None:
    """Publish every verification command in a body; return what is left of it.

    ``None`` when the body was nothing but verification. Meta may batch several
    messages into one delivery, and the check used to read only the first: a
    code sent behind an ordinary message was answered as chat and never
    verified, and one sent in front of an ordinary message swallowed it -- the
    whole delivery returned as "verification received" and the other message
    was never seen.

    Each message is tested as a single-message body, the shape the identity
    parser reads, and the rest of the body -- other messages, statuses -- is
    kept exactly as it came.
    """
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return payload
    kept_entries: list[JsonValue] = []
    removed = 0
    for entry in entries:
        kept_entry, published = await _entry_without_verifications(
            entry, uow_factory, publish or _published_whatsapp_verification
        )
        removed += published
        kept_entries.append(kept_entry)
    if not removed:
        return payload
    remaining: dict[str, JsonValue] = {**payload, "entry": kept_entries}
    return remaining if _carries_anything(kept_entries) else None


#: Publishes a single-message body when it is a verification command.
VerificationPublisher = Callable[
    [dict[str, JsonValue], UnitOfWorkFactory], Awaitable[bool]
]


async def _entry_without_verifications(
    entry: JsonValue, uow_factory: UnitOfWorkFactory, publish: VerificationPublisher
) -> tuple[JsonValue, int]:
    changes = entry.get("changes") if isinstance(entry, dict) else None
    if not isinstance(entry, dict) or not isinstance(changes, list):
        return entry, 0
    kept_changes: list[JsonValue] = []
    published = 0
    for change in changes:
        value = change.get("value") if isinstance(change, dict) else None
        messages = value.get("messages") if isinstance(value, dict) else None
        if not (
            isinstance(change, dict)
            and isinstance(value, dict)
            and isinstance(messages, list)
        ):
            kept_changes.append(change)
            continue
        kept: list[JsonValue] = []
        for message in messages:
            single_change = {**change, "value": {**value, "messages": [message]}}
            single: dict[str, JsonValue] = {
                "entry": [{**entry, "changes": [single_change]}]
            }
            if await publish(single, uow_factory):
                published += 1
            else:
                kept.append(message)
        kept_changes.append({**change, "value": {**value, "messages": kept}})
    return {**entry, "changes": kept_changes}, published


def _carries_anything(entries: list[JsonValue]) -> bool:
    """Whether what is left still holds a message or a status worth publishing."""
    for entry in entries:
        changes = entry.get("changes") if isinstance(entry, dict) else None
        for change in changes if isinstance(changes, list) else []:
            value = change.get("value") if isinstance(change, dict) else None
            if isinstance(value, dict) and (
                value.get("messages") or value.get("statuses")
            ):
                return True
    return False


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
    from app.modules.agent_surfaces.composition import (
        build_app_event_handler,
    )

    async with uow_factory() as uow:
        return await build_app_event_handler(uow).try_handle_channel_setup(
            SurfacePlatformWebhookIngress(
                source="slack",
                payload=payload,
                headers=_redacted_headers(headers),
                receiver_surface_ids=receiver_surface_ids,
            )
        )
