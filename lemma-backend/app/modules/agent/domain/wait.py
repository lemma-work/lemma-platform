"""Agent conversation waits: what a suspended conversation is waiting on.

Deliberately shape-identical to :mod:`app.modules.workflow.domain.wait` — same
statuses, same ``external_ref`` discipline, same "the wait row is the source of
truth, never the context" rule. The reconciliation sweep for lost timers is the
same sweep, so the two staying alike is worth more than either being
individually tidier.

A conversation wait is *not* a schedule. A schedule is a standing rule someone
configured; this is one suspended execution that will resolve exactly once.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.domain.aggregate import AggregateRoot


class AgentWaitType(str, Enum):
    """What the conversation is waiting on.

    Only TIME today. Waking on a record change was scoped out deliberately:
    reacting to a row changing is what a DATASTORE trigger is for, and a second
    path to the same event is a duplication this codebase can do without. If it
    comes back it arrives as a new member here, not as a flag on this one.

    Human pauses are also absent: they resolve through the approval-decision
    row, which records *who* decided. Adding them would mean two sources of
    truth for one pause.
    """

    TIME = "TIME"


class AgentWaitStatus(str, Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    # Terminal, and the only one nobody asked for: the wake kept raising. Without
    # it a wait whose wake can never succeed stays ACTIVE and past-due forever,
    # and the sweep retries it every five minutes for the life of the deployment.
    FAILED = "FAILED"


class AgentWaitWakeReason(str, Enum):
    """Why the agent woke. Handed back to the model in the tool return.

    ANSWERED is deliberately a *reason*, not a second :class:`AgentWaitType`.
    The wait is still a timer — armed with a ``scheduled_at``, swept by the same
    reconciliation — and the timer stays the guarantee that the agent comes back
    at all. An answer landing only resolves that timer sooner. Modelling it as a
    wait type would mean a wait that resolves on two unrelated conditions, and
    every sweep and claim path would have to learn about the second one.
    """

    TIMER = "TIMER"
    CANCELLED = "CANCELLED"
    # Everything this conversation asked a person with `message_user` has been
    # answered. Unlike TIMER this *does* say something happened.
    ANSWERED = "ANSWERED"


class AgentConversationWaitEntity(AggregateRoot):
    """A queryable wait owned by a paused agent conversation."""

    conversation_id: UUID
    agent_run_id: UUID
    pod_id: UUID
    tool_call_id: str

    wait_type: AgentWaitType = AgentWaitType.TIME
    status: AgentWaitStatus = AgentWaitStatus.ACTIVE

    external_ref: str | None = None
    scheduled_at: datetime | None = None
    # Sweep bookkeeping. Incremented in its own transaction *before* each retry,
    # so it survives the rollback of the attempt it is counting.
    wake_attempts: int = 0

    # The snooze request verbatim, plus whatever the wake recorded.
    spec: dict[str, Any] = Field(default_factory=dict)
    completed_at: datetime | None = None

    def complete(self, reason: AgentWaitWakeReason) -> None:
        self.status = AgentWaitStatus.COMPLETED
        self.spec = {**self.spec, "woke_because": reason.value}
        self.completed_at = datetime.now(timezone.utc)

    def cancel(self) -> None:
        self.status = AgentWaitStatus.CANCELLED
        self.spec = {**self.spec, "woke_because": AgentWaitWakeReason.CANCELLED.value}
        self.completed_at = datetime.now(timezone.utc)

    def abandon(self, error: str) -> None:
        """Give up after too many failed wakes. The conversation stays WAITING.

        Deliberately not a wake: we could not synthesize a tool return, so there
        is nothing truthful to hand the model. The row records why, and the error
        log is the operator's signal — a stuck conversation is bad, but a hot
        retry loop that hides it is worse.
        """
        self.status = AgentWaitStatus.FAILED
        self.spec = {**self.spec, "abandoned_because": error}
        self.completed_at = datetime.now(timezone.utc)
