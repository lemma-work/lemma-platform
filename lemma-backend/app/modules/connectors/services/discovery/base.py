"""Shared types for connector operation discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DiscoveredOperation:
    """A single operation discovered for an auth-config, ready to upsert."""

    name: str
    display_name: str | None
    description: str | None
    input_schema: dict[str, Any] | None
    output_schema: dict[str, Any] | None
    execution: dict[str, Any]
    tags: tuple[str, ...] = field(default_factory=tuple)


def normalize_operation_name(name: str) -> str:
    """Normalize a provider tool/operation name to a stable public op name."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", (name or "").strip()).strip("_").lower()
    return slug or "operation"


def assign_unique_names(names: list[str]) -> list[str]:
    """Normalize a list of provider names, disambiguating any collisions.

    Normalization is lossy: a server offering both ``Get User`` and ``get_user``
    produces ``get_user`` twice. Those two rows then collide on the install's
    unique index, which used to abort the whole re-discovery -- after the delete
    had already run, leaving the install with no operations at all. Suffixing is
    deterministic and order-stable, so a name stays put across refreshes as long
    as the server keeps returning its tools in the same order.
    """
    assigned: list[str] = []
    seen: dict[str, int] = {}
    for raw in names:
        base = normalize_operation_name(raw)
        count = seen.get(base, 0)
        seen[base] = count + 1
        assigned.append(base if count == 0 else f"{base}_{count + 1}")
    return assigned
