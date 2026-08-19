"""Shared primitives for native surface receivers (pollers / socket clients).

Kept apart from ``event_receiver_service`` so a per-platform runner module can
depend on the candidate shape and the receiver key without importing the
coordinator — which imports the runners, so the reverse would be a cycle.
"""

from __future__ import annotations

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
