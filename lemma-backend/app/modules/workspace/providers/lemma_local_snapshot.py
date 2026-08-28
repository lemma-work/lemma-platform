"""Reading a guest sandbox snapshot.

The guest answers two shapes for the same thing: `sandbox.status` returns a
snapshot directly, while a `sandbox.list` entry wraps one. Every question about
"what is this sandbox doing" therefore has to cope with both, and putting those
readers here keeps that knowledge in one place instead of spread through the
provider.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

_LIVE_STATES = {"running", "ready"}


def state_of(snapshot: dict[str, Any]) -> str:
    """The guest's own word for what this sandbox is doing, for error text."""
    status = snapshot.get("status")
    if isinstance(status, dict):
        return str(status.get("state") or status.get("status") or "unknown")
    return str(snapshot.get("state", "unknown"))


def is_running(snapshot: dict[str, Any]) -> bool:
    return state_of(snapshot).lower() in _LIVE_STATES


def is_serving(snapshot: dict[str, Any]) -> bool:
    """Whether the guest reports the sandbox's own runtime as answering.

    Distinct from `is_running`: a container can be up while what it hosts is
    still starting, and treating those as the same is how a sandbox came to be
    reported ready before it could serve anything.
    """
    status = snapshot.get("status")
    return bool(status.get("ready")) if isinstance(status, dict) else False


def guest_id_of(entry: dict[str, Any]) -> str | None:
    """The guest id of one `sandbox.list` entry.

    A list entry wraps the snapshot: the id lives at ``status.id``, while
    ``sandbox.status`` returns that snapshot unwrapped. Both shapes are read
    here so a caller never has to know which call produced the dict.
    """
    status = entry.get("status")
    if isinstance(status, dict):
        nested = status.get("id")
        if isinstance(nested, str) and nested:
            return nested
    for key in ("sandbox_id", "id"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def sandbox_id_from_guest_id(guest_id: str) -> UUID | None:
    prefix, _, raw = guest_id.partition("-")
    if prefix not in {"w", "f"}:
        return None
    try:
        return UUID(hex=raw)
    except ValueError:
        return None
