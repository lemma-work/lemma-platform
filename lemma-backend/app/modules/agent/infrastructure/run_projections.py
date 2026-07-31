"""Minimal database projections used by agent-run maintenance operations."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class StaleAgentRunRef:
    """Stable identity needed to reconcile an orphaned run."""

    id: UUID
    conversation_id: UUID
