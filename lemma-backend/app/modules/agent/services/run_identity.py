"""Which run this is, and what it needs to be billed and finished.

Eight fields that never travel apart. They were threaded individually through
`_handle_harness_event` (fifteen keyword parameters, eight of them these) and
through `_finish_agent_run` at five separate call sites, which meant adding one
piece of run identity meant editing six signatures and hoping every caller was
found.

Resolved once when the run starts and passed whole after that.
"""

from __future__ import annotations

from dataclasses import dataclass
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

    def with_reservation(self, reservation: UsageReservation | None) -> "RunIdentity":
        """The same run, once usage has been reserved for it.

        The reservation is made after the context is built but before the model
        is called, so it is the one field that arrives late.
        """
        return RunIdentity(
            conversation_id=self.conversation_id,
            agent_run_id=self.agent_run_id,
            organization_id=self.organization_id,
            pod_id=self.pod_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            started_at=self.started_at,
            runtime_profile=self.runtime_profile,
            usage_reservation=reservation,
        )

    def with_runtime_profile(
        self, snapshot: dict[str, object | None] | None
    ) -> "RunIdentity":
        """The same run, once its runtime profile has been resolved."""
        return RunIdentity(
            conversation_id=self.conversation_id,
            agent_run_id=self.agent_run_id,
            organization_id=self.organization_id,
            pod_id=self.pod_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            started_at=self.started_at,
            runtime_profile=snapshot,
            usage_reservation=self.usage_reservation,
        )
