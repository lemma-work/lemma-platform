"""What the profile picker offers: a pod the shared bot could answer from."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AvailablePod:
    """A pod the person could choose to be answered from on the shared bot.

    A plain value in the domain rather than beside the adapter that lists them,
    because `user_surfaces_service` names it in its port -- and importing the
    adapter's module for a type pulled all of chat provisioning into the
    worker's import graph.
    """

    pod_id: UUID
    name: str
    organization_name: str | None
