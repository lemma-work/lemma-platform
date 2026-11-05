"""Who a call is for, as everything that bills or authorizes it needs to know."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CallParty:
    user_id: UUID
    organization_id: UUID | None
    pod_id: UUID
    conversation_id: UUID
