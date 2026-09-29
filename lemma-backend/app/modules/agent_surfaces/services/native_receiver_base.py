"""Shared primitives for native surface receivers (pollers / socket clients).

Kept apart from ``event_receiver_service`` so a per-platform runner module can
depend on the candidate shape and the receiver key without importing the
coordinator — which imports the runners, so the reverse would be a cycle.
"""

from __future__ import annotations

from app.core.infrastructure.events.inbox import stable_event_id
from app.core.infrastructure.events.publisher import EventPublisher
from app.modules.agent_surfaces.domain.events import SurfaceWebhookReceivedEvent
from app.modules.agent_surfaces.domain.source_event_ids import native_source_event_id

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.modules.agent_surfaces.domain.entities import SurfacePlatform


class NativeReceiverConflict(RuntimeError):
    """Another consumer already owns this upstream credential.

    The lease this service takes is a Redis key, so it only orders the workers
    of *one* deployment. Nothing stops a Desktop installation and Lemma Cloud
    from being configured with the same Telegram bot or Resend inbox, and each
    then takes its own lease from its own Redis and starts consuming.

    Upstream is the only place that can see both. Telegram says so outright --
    a second ``getUpdates`` gets 409 -- so a runner that has been refused for
    longer than a handover could explain raises this rather than returning as
    though it had finished its work.
    """


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
    # The receiver is part of the identity, because a provider's id is only
    # unique within one.
    source_event_id = native_source_event_id(source, payload, receiver_key=receiver_key)
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
