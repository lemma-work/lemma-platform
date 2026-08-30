"""Minimal database projections used by agent-run maintenance operations."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class StaleAgentRunRef:
    """Stable identity needed to reconcile an orphaned run."""

    id: UUID
    conversation_id: UUID


@dataclass(frozen=True, slots=True)
class StrandedConversationRef:
    """A conversation left active by a run that has already finished.

    The other half of orphan reconciliation. ``StaleAgentRunRef`` finds runs
    nothing finished; this finds conversations whose run *was* finished while
    the conversation was not. A sweep keyed on run status cannot see one, and
    nothing else will ever move it: the run is terminal, so it is never
    finalized again.

    Carries the run's own status so the conversation is settled as what
    actually happened rather than assumed to have completed.
    """

    id: UUID
    run_status: str
