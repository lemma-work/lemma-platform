"""A principal a grant or role can name: a person, a role, a workload, a pod."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PrincipalRef:
    type: str
    id: UUID
