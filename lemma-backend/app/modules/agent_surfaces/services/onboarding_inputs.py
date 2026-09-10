"""Opaque native-input handles tied to the persisted private signup destination."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    OnboardingInputToken,
    PendingChatOnboarding,
)
from app.modules.agent_surfaces.services.onboarding_private_delivery import (
    PrivateDeliveryUnavailable,
)

CALLBACK = "lemma_onboarding"
OPEN_ACTION = "lemma_onboarding_open"


def section(payload: dict[str, JsonValue], key: str) -> dict[str, JsonValue]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def is_onboarding_input(payload: dict[str, JsonValue]) -> bool:
    if section(payload, "view").get("callback_id") == CALLBACK:
        return True
    if "lemma_onboarding_token" in section(payload, "value"):
        return True
    actions = payload.get("actions")
    return isinstance(actions, list) and any(
        isinstance(action, dict) and action.get("action_id") == OPEN_ACTION
        for action in actions
    )


@dataclass(frozen=True, slots=True)
class BoundInput:
    pending_id: UUID
    installation_id: UUID | None
    destination: ParsedInboundSurfaceEvent
    step: str


async def require_input(
    uows: UnitOfWorkFactory,
    *,
    token: str,
    platform: SurfacePlatform,
    actor: str,
    tenant: str,
    receiver_ids: list[UUID] | None,
) -> BoundInput:
    if not token or not actor:
        raise PrivateDeliveryUnavailable("This setup form is no longer available")
    async with uows() as uow:
        handle = await uow.session.scalar(
            select(OnboardingInputToken).where(
                OnboardingInputToken.token_hash
                == hashlib.sha256(token.encode()).hexdigest()
            )
        )
        pending = (
            await uow.session.get(PendingChatOnboarding, handle.pending_id)
            if handle
            else None
        )
        pending = _require_current_handle(handle, pending)
        destination = ParsedInboundSurfaceEvent.model_validate(pending.destination)
        if (
            destination.platform != platform
            or not destination.is_dm
            or destination.sender_external_user_id != actor
            or (destination.tenant_id or "") != tenant
            or (
                receiver_ids is not None
                and platform in (SurfacePlatform.SLACK, SurfacePlatform.TEAMS)
                and pending.installation_surface_id not in receiver_ids
            )
        ):
            raise PrivateDeliveryUnavailable(
                "This setup form belongs to another sender or installation"
            )
        return BoundInput(
            pending.id, pending.installation_surface_id, destination, pending.step
        )


async def native_prompt_metadata(
    uows: UnitOfWorkFactory,
    *,
    binding_key: str,
    platform: SurfacePlatform,
) -> dict[str, JsonValue]:
    if platform not in (
        SurfacePlatform.SLACK,
        SurfacePlatform.TEAMS,
        SurfacePlatform.WHATSAPP,
    ):
        return {}
    async with uows() as uow:
        pending = await uow.session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        if pending is None or pending.step not in ("awaiting_email", "awaiting_code"):
            return {}
        token = secrets.token_urlsafe(32)
        uow.session.add(
            OnboardingInputToken(
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                pending_id=pending.id,
                step=pending.step,
                expires_at=pending.expires_at,
                challenge_id=pending.challenge_id,
            )
        )
        label = (
            "Email address" if pending.step == "awaiting_email" else "Verification code"
        )
        prefill = (
            ParsedInboundSurfaceEvent.model_validate(pending.destination).sender_email
            or ""
        )
        flow_id = (
            surface_settings.whatsapp_onboarding_email_flow_id
            if pending.step == "awaiting_email"
            else surface_settings.whatsapp_onboarding_code_flow_id
        )
    if platform == SurfacePlatform.WHATSAPP:
        if not flow_id:
            return {}
        return {
            "onboarding_flow": {
                "type": "flow",
                "body": {
                    "text": f"Enter your {label.lower()} to continue. You can also reply here."
                },
                "action": {
                    "name": "flow",
                    "parameters": {
                        "flow_message_version": "3",
                        "flow_id": flow_id,
                        "flow_token": token,
                        "flow_cta": "Continue",
                        "flow_action": "navigate",
                        "flow_action_payload": {"screen": "ONBOARDING"},
                    },
                },
            }
        }
    if platform == SurfacePlatform.SLACK:
        return {
            "onboarding_blocks": [
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "action_id": OPEN_ACTION,
                            "value": token,
                            "text": {
                                "type": "plain_text",
                                "text": f"Enter {label.lower()}",
                            },
                        }
                    ],
                }
            ]
        }
    return {
        "onboarding_card": {
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [
                {
                    "type": "Input.Text",
                    "id": "answer",
                    "label": label,
                    "isRequired": True,
                    "maxLength": 254,
                    "value": prefill if label == "Email address" else "",
                }
            ],
            "actions": [
                {
                    "type": "Action.Submit",
                    "title": "Continue",
                    "data": {"lemma_onboarding_token": token},
                }
            ],
        }
    }


def slack_modal(token: str, step: str, *, prefill: str = "") -> dict[str, JsonValue]:
    label = "Email address" if step == "awaiting_email" else "Six-digit code"
    return {
        "type": "modal",
        "callback_id": CALLBACK,
        "private_metadata": token,
        "title": {"type": "plain_text", "text": "Continue with Lemma"},
        "submit": {"type": "plain_text", "text": "Continue"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "answer",
                "label": {"type": "plain_text", "text": label},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "answer",
                    "max_length": 254 if step == "awaiting_email" else 6,
                    "initial_value": prefill if step == "awaiting_email" else "",
                },
            }
        ],
    }


def _require_current_handle(
    handle: OnboardingInputToken | None, pending: PendingChatOnboarding | None
) -> PendingChatOnboarding:
    now = datetime.now(timezone.utc)
    if (
        handle is None
        or pending is None
        or handle.expires_at <= now
        or pending.expires_at <= now
        or pending.step != handle.step
        or pending.challenge_id != handle.challenge_id
        or pending.handed_off_at is not None
    ):
        raise PrivateDeliveryUnavailable(
            "This setup form expired; continue in your private chat"
        )
    return pending
