"""Which run this is, and what it needs to be billed and finished.

The fields that never travel apart. They were threaded individually through
`_handle_harness_event` (fifteen keyword parameters, eight of them these) and
through `_finish_agent_run` at five separate call sites, which meant adding one
piece of run identity meant editing six signatures and hoping every caller was
found.

Resolved once when the run starts and passed whole after that.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.origin import Origin, current_origin
from app.modules.agent.domain.run_waiters import run_work_origin
from app.modules.usage.contracts import UsageReservation

if TYPE_CHECKING:
    from app.modules.agent.domain.entities import AgentRun, Conversation


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
    #: What the run's usage is recorded under: an ordinary agent run, or one
    #: answering somebody outside the pod, which spends the contacts cap and
    #: never the allowance of the member looking after the conversation.
    #: Carried here so the terminal write records it the same way the
    #: in-flight requests were.
    usage_source: str = "agent_run"
    #: The run was told its memory is kept after it ends, so its terminal write
    #: owes it the memory pass. Carried from the run's context rather than
    #: re-derived at the finish, so the pass follows exactly the runs whose
    #: prompt promised one.
    memory_after_turn: bool = False
    #: How the run's work arrived, for the completion event. Resolved where the
    #: run starts, because the finalizer runs in a work item that has no origin
    #: of its own to inherit (``domain.run_waiters.run_work_origin``).
    origin: Origin | None = None

    @classmethod
    def of_turn(
        cls,
        conversation: Conversation,
        agent_run: AgentRun,
        *,
        agent_run_id: UUID,
        user_id: UUID,
        usage_source: str,
        platform: str | None = None,
    ) -> "RunIdentity":
        """The identity of ``agent_run``, a turn of ``conversation``, as it starts.

        Its origin is the request's when the turn runs inside one; otherwise --
        the usual case, a work item -- what the run's kind says, and the
        ``platform`` its surface records.
        """
        return cls(
            conversation_id=conversation.id,
            agent_run_id=agent_run_id,
            organization_id=conversation.organization_id,
            pod_id=conversation.pod_id,
            user_id=user_id,
            agent_id=conversation.agent_id,
            started_at=agent_run.started_at,
            usage_source=usage_source,
            origin=current_origin()
            or run_work_origin(agent_run.state.kind, conversation, platform=platform),
        )

    @property
    def origin_kind(self) -> str | None:
        """``origin`` as an event records it."""
        return self.origin.kind.value if self.origin else None

    @property
    def origin_platform(self) -> str | None:
        return self.origin.platform if self.origin else None

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

    def with_memory_after_turn(self, memory_after_turn: bool) -> "RunIdentity":
        """The same run, once its context has said where its memory is kept."""
        return replace(self, memory_after_turn=memory_after_turn)
