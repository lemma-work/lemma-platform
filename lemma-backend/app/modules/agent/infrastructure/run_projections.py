"""Minimal database projections used by agent-run maintenance operations."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class StaleAgentRunRef:
    """Stable identity needed to reconcile an orphaned run."""

    id: UUID
    conversation_id: UUID


@dataclass(frozen=True, slots=True)
class ResumableAgentRunRef:
    """A parked run, plus what enqueueing it again requires."""

    id: UUID
    conversation_id: UUID
    user_id: UUID
    pod_id: UUID
    resume_attempts: int
