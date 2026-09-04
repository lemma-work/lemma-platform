"""Which run this is, and what it needs to be billed and finished.

Eight fields that never travel apart. They were threaded individually through
`_handle_harness_event` (fifteen keyword parameters, eight of them these) and
through `_finish_agent_run` at five separate call sites, which meant adding one
piece of run identity meant editing six signatures and hoping every caller was
found.

Resolved once when the run starts and passed whole after that.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from app.modules.usage.contracts import UsageReservation


@dataclass(frozen=True, slots=True)
class RunIdentity:
    """Everything a terminal write needs that is fixed for the whole run."""

    conversation_id: UUID
    agent_run_id: UUID
    organization_id: UUID | None = None
    pod_id: UUID | None = None
    user_id: UUID | None = None
    agent_id: UUID | None = None
    started_at: datetime | None = None
    runtime_profile: dict[str, object | None] | None = None
    usage_reservation: UsageReservation | None = None
    #: This worker's turn at the run, which is not the same as the run. A
    #: reclaimed run keeps its id and gets a new attempt, so spend recorded
    #: under one attempt cannot be overwritten by the next.
    attempt_id: str | None = None

    # `replace` rather than reconstructing field by field: the hand-written
    # copies meant adding a field to this class silently dropped it from every
    # run that had been through one of them.
    def with_reservation(self, reservation: UsageReservation | None) -> "RunIdentity":
        """The same run, once usage has been reserved for it.

        The reservation is made after the context is built but before the model
        is called, so it is the one field that arrives late.
        """
        return replace(self, usage_reservation=reservation)

    def with_runtime_profile(
        self, snapshot: dict[str, object | None] | None
    ) -> "RunIdentity":
        """The same run, once its runtime profile has been resolved."""
        return replace(self, runtime_profile=snapshot)

    def with_attempt(self, attempt_id: str) -> "RunIdentity":
        """The same run, under this worker's turn at it."""
        return replace(self, attempt_id=attempt_id)
