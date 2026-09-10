"""Validate native submissions using authenticated platform actor fields."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from pydantic import JsonValue, TypeAdapter, ValidationError

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.services.onboarding_inputs import (
    CALLBACK,
    require_input,
    section,
)
from app.modules.agent_surfaces.services.onboarding_private_delivery import (
    PrivateDeliveryUnavailable,
)


def _first_section(payload: dict[str, JsonValue], key: str) -> dict[str, JsonValue]:
    values = payload.get(key)
    return (
        values[0]
        if isinstance(values, list) and len(values) == 1 and isinstance(values[0], dict)
        else {}
    )


async def parse_native_submission(
    payload: dict[str, JsonValue],
    *,
    platform: SurfacePlatform,
    uows: UnitOfWorkFactory,
    adapters: SurfacePlatformAdapterRegistry,
    receiver_ids: list[UUID] | None,
) -> ParsedInboundSurfaceEvent | None:
    event: ParsedInboundSurfaceEvent | None = None
    if platform == SurfacePlatform.SLACK:
        submission = _slack_submission(payload)
    elif platform == SurfacePlatform.TEAMS:
        submission = await _teams_submission(payload, adapters)
    elif platform == SurfacePlatform.WHATSAPP:
        submission = await _whatsapp_submission(payload, adapters)
    else:
        return None
    if submission is None:
        return None
    token, answer, actor, tenant, event = (
        submission.token,
        submission.answer,
        submission.actor,
        submission.tenant,
        submission.event,
    )
    if not all(isinstance(item, str) for item in (token, answer, actor, tenant)):
        raise PrivateDeliveryUnavailable("Incomplete setup submission")
    assert (
        isinstance(token, str)
        and isinstance(answer, str)
        and isinstance(actor, str)
        and isinstance(tenant, str)
    )
    bound = await require_input(
        uows,
        token=token,
        platform=platform,
        actor=actor,
        tenant=tenant,
        receiver_ids=receiver_ids,
    )
    if (
        event is not None
        and event.external_channel_id != bound.destination.external_channel_id
    ):
        raise PrivateDeliveryUnavailable(
            "Submit setup in its original personal conversation"
        )
    answer = answer.strip()
    if bound.step == "awaiting_email":
        try:
            answer = validate_email(answer, check_deliverability=False).normalized
        except EmailNotValidError as error:
            raise PrivateDeliveryUnavailable("Enter a valid email address") from error
    elif not re.fullmatch(r"[0-9]{6}", answer):
        raise PrivateDeliveryUnavailable("Enter exactly six digits")
    return bound.destination.model_copy(update={"message_text": answer})


@dataclass(frozen=True, slots=True)
class _NativeSubmission:
    token: JsonValue | None
    answer: JsonValue | None
    actor: JsonValue | None
    tenant: JsonValue | None
    event: ParsedInboundSurfaceEvent | None


def _slack_submission(payload: dict[str, JsonValue]) -> _NativeSubmission | None:
    event: ParsedInboundSurfaceEvent | None = None
    view = section(payload, "view")
    if payload.get("type") != "view_submission" or view.get("callback_id") != CALLBACK:
        return None
    token = view.get("private_metadata")
    values = section(section(view, "state"), "values")
    block = section(values, "answer")
    entry = section(block, "answer")
    if set(values) != {"answer"} or set(block) != {"answer"}:
        raise PrivateDeliveryUnavailable("Unexpected setup form fields")
    answer = entry.get("value")
    actor = section(payload, "user").get("id")
    tenant = section(payload, "team").get("id")
    return _NativeSubmission(token, answer, actor, tenant, event)


async def _teams_submission(
    payload: dict[str, JsonValue], adapters: SurfacePlatformAdapterRegistry
) -> _NativeSubmission | None:
    event: ParsedInboundSurfaceEvent | None = None
    platform = SurfacePlatform.TEAMS
    value = section(payload, "value")
    if "lemma_onboarding_token" not in value:
        return None
    if set(value) != {"lemma_onboarding_token", "answer"}:
        raise PrivateDeliveryUnavailable("Unexpected setup form fields")
    token, answer = value.get("lemma_onboarding_token"), value.get("answer")
    adapter = adapters.get(platform)
    assert adapter is not None
    event = await adapter.parse_inbound_event(
        {**payload, "type": "message", "text": "setup", "value": {}}, {}
    )
    if event is None or not event.is_dm:
        raise PrivateDeliveryUnavailable("Submit setup in the personal bot chat")
    actor, tenant = event.sender_external_user_id, event.tenant_id
    return _NativeSubmission(token, answer, actor, tenant, event)


async def _whatsapp_submission(
    payload: dict[str, JsonValue], adapters: SurfacePlatformAdapterRegistry
) -> _NativeSubmission | None:
    event: ParsedInboundSurfaceEvent | None = None
    platform = SurfacePlatform.WHATSAPP
    envelope = section(
        _first_section(_first_section(payload, "entry"), "changes"), "value"
    )
    message = _first_section(envelope, "messages")
    response = section(section(message, "interactive"), "nfm_reply").get(
        "response_json"
    )
    if response is None:
        return None
    if not isinstance(response, str) or len(response) > 2048:
        raise PrivateDeliveryUnavailable("Invalid setup response")
    try:
        value = TypeAdapter(dict[str, JsonValue]).validate_json(response)
    except ValidationError as error:
        raise PrivateDeliveryUnavailable("Invalid setup response") from error
    if set(value) != {"flow_token", "answer"}:
        raise PrivateDeliveryUnavailable("Unexpected setup form fields")
    token, answer = value.get("flow_token"), value.get("answer")
    adapter = adapters.get(platform)
    assert adapter is not None
    event = await adapter.parse_inbound_event(payload, {})
    if event is None:
        raise PrivateDeliveryUnavailable("The platform did not identify the sender")
    actor, tenant = event.sender_external_user_id, event.tenant_id or ""
    return _NativeSubmission(token, answer, actor, tenant, event)
