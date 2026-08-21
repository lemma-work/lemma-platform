"""Shared primitives for native surface receivers (pollers / socket clients).

Kept apart from ``event_receiver_service`` so a per-platform runner module can
depend on the candidate shape and the receiver key without importing the
coordinator — which imports the runners, so the reverse would be a cycle.
"""

from __future__ import annotations

import json

from app.core.infrastructure.events.inbox import stable_event_id
from app.core.infrastructure.events.publisher import EventPublisher
from app.modules.agent_surfaces.domain.events import SurfaceWebhookReceivedEvent

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.modules.agent_surfaces.domain.entities import SurfacePlatform


class ReceiverRunner(Protocol):
    async def run(self) -> None:
        """Run the receiver until it is cancelled."""


ReceiverRunnerFactory = Callable[["NativeReceiverCandidate"], ReceiverRunner]


@dataclass(frozen=True)
class NativeReceiverCandidate:
    key: str
    platform: SurfacePlatform
    surface_ids: tuple[UUID, ...]
    credential_label: str
    credentials: dict[str, Any]


def receiver_key(platform: str, label: str, secret: str) -> str:
    """A stable, opaque per-credential id for lease/dedup keys — not a password.

    The credential is the HMAC *key*, not the hashed input: this is a keyed
    fingerprint that identifies a credential group and detects rotation without
    storing or exposing the secret. A bare hash of the credential would read to
    scanners as (weak) password hashing, which this deliberately is not.
    """
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{platform}:{label}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:24]
    return f"{platform}:{label}:{digest}"


async def _publish_native_receiver_event(
    *,
    source: str,
    payload: dict[str, Any],
    receiver_key: str | None,
    surface_ids: "tuple[UUID, ...] | None" = None,
) -> None:
    headers = {"x-lemma-surface-event-mode": "native_receiver"}
    if receiver_key:
        headers["x-lemma-surface-receiver-key"] = receiver_key
    provider_id = (
        payload.get("event_id")
        or payload.get("update_id")
        or payload.get("id")
        or hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()
    )
    source_event_id = f"{source}:native:{provider_id}"
    event = SurfaceWebhookReceivedEvent(
        event_id=stable_event_id({"event_id": source_event_id}),
        source=source,
        payload=payload,
        headers=headers,
        source_event_id=source_event_id,
        # Scope downstream ingress to the surfaces this bot actually serves, so a
        # custom bot's update can't be mis-attributed to another bot's surface.
        receiver_surface_ids=list(surface_ids) if surface_ids else None,
    )
    await EventPublisher.publish(event.stream_name(), event)
