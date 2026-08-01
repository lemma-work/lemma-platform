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
