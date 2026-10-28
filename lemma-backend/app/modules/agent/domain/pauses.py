"""A run stopped until something answers it: the ``Pause`` aggregate.

Every way a run waits is one of these, one row of ``agent_pauses`` keyed by the
tool call that opened it: a question, an approval, a sign-in, a spent budget,
an Agent Host permission request, a ``wait_for``. The call's message is still
what a card renders from and what history replays; the pause is what decides
whether it is waiting, and on what.

A pause ends once. Each transition below applies only to a PENDING pause and
says whether it did; the repository writes it fenced on the status it was read
with, so of two callers racing to end the same pause exactly one wins and the
other adopts what the winner recorded.

A pause is *open* while it is PENDING and no return has landed for its call.
Its own return closes one that never paused -- an approval granted earlier, a
question that would not validate -- as CANCELLED (``Pauses.close_by_return``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from app.core.domain.aggregate import AggregateRoot
from app.modules.agent.domain.approvals import (
    ANSWERED_BY_REPLY,
    EXPIRED_UNANSWERED,
    SUPERSEDED_KEY,
)
from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.pause_opening import (
    AgentHostPermissionRequest,
    BudgetSpent,
    PauseOpening,
)
from app.modules.agent.domain.state_kinds import (
    AgentRunApprovalDecision,
    PauseKind,
    PauseStatus,
)
from app.modules.agent.domain.wait import AgentWaitType, AgentWaitWakeReason, WaitSpec

#: How long a question waits for its answer, and an approval for its decision,
#: before it expires as a DENY (the channel question line is the clock).
ASK_EXPIRES_AFTER = timedelta(hours=72)
APPROVAL_EXPIRES_AFTER = timedelta(days=7)

#: The pauses a person resolves. A wait resolves on its own, with nobody
#: involved, so it is never on an approvals list or routed a typed reply.
PERSON_KINDS = frozenset(
    {
        PauseKind.QUESTION,
        PauseKind.APPROVAL,
        PauseKind.SIGN_IN,
        PauseKind.BUDGET,
        PauseKind.HOST_PERMISSION,
    }
)
#: Stored as a ``request_approval`` call: what a surface renders as a decision.
APPROVAL_KINDS = frozenset(
    {PauseKind.APPROVAL, PauseKind.BUDGET, PauseKind.HOST_PERMISSION}
)
#: What a person is asked, on a line and in their inbox. A sign-in is not: its
#: own link says where to go.
ASKED_KINDS = APPROVAL_KINDS | {PauseKind.QUESTION}
ALL_KINDS = frozenset(PauseKind)
#: The name each kind's call is stored under. Read only where a transcript is
#: read without its pauses to hand -- history replay, the Agent Host's drop of
#: a call Lemma already recorded -- and checked against ``ToolPolicy.pauses``
#: (``test_replay_registry``). Whether something is waiting reads the Pause.
CALL_NAMES: dict[PauseKind, str] = {
    PauseKind.QUESTION: "ask_user",
    PauseKind.APPROVAL: "request_approval",
    PauseKind.SIGN_IN: "browser_sign_in",
    PauseKind.WAIT: "wait_for",
    PauseKind.BUDGET: "request_approval",
    PauseKind.HOST_PERMISSION: "request_approval",
}
PAUSING_CALL_NAMES = frozenset(CALL_NAMES.values())
#: Somebody decided it (or the system did, for them); its return may still be
#: on its way -- an approved tool runs in a worker first.
DECIDED = frozenset({PauseStatus.RESOLVED, PauseStatus.EXPIRED, PauseStatus.SUPERSEDED})


def deadline_for(kind: PauseKind, opened_at: datetime) -> datetime | None:
    """When a pause of ``kind`` opened at ``opened_at`` expires, if it does."""
    if kind is PauseKind.QUESTION:
        return opened_at + ASK_EXPIRES_AFTER
    if kind in APPROVAL_KINDS:
        return opened_at + APPROVAL_EXPIRES_AFTER
    return None


class PauseResolution(BaseModel):
    """How a pause ended: a person's decision, or why a wait woke."""

    model_config = ConfigDict(frozen=True)

    decision: AgentRunApprovalDecision | None = None
    #: The answers, a note, ``working`` for a sign-in -- and the marker that
    #: says nobody decided (``domain/approvals``).
    response: dict[str, object] = Field(default_factory=dict)
    resolved_by_user_id: UUID | None = None
    woke_because: AgentWaitWakeReason | None = None


class Pause(AggregateRoot):
    conversation_id: UUID
    run_id: UUID
    pod_id: UUID
    tool_call_id: str
    #: The name the call is stored under, which its return is written under.
    tool_name: str
    kind: PauseKind
    status: PauseStatus = PauseStatus.PENDING
    deadline: datetime | None = None
    resolution: PauseResolution | None = None
    host_permission: AgentHostPermissionRequest | None = None
    budget: BudgetSpent | None = None
    #: A wait's own: what it watches, its token, when its timer is next due.
    wait_type: AgentWaitType | None = None
    external_ref: str | None = None
    wake_at: datetime | None = None
    wait_spec: WaitSpec | None = None
    execution_claimed_at: datetime | None = None
    resolved_at: datetime | None = None
    #: Read with the pause, never written by it: the call that opened it, and
    #: whether a return has landed for that call.
    call: Message | None = Field(default=None, exclude=True)
    returned: bool = Field(default=False, exclude=True)

    _read_as: PauseStatus = PrivateAttr(default=PauseStatus.PENDING)

    @classmethod
    def opened_by(
        cls,
        opening: PauseOpening,
        *,
        conversation_id: UUID,
        run_id: UUID,
        pod_id: UUID,
        tool_call_id: str,
        tool_name: str,
    ) -> Pause:
        """The pause a tool call opens, written with the call."""
        now = datetime.now(timezone.utc)
        return cls(
            conversation_id=conversation_id,
            run_id=run_id,
            pod_id=pod_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            kind=opening.kind,
            deadline=deadline_for(opening.kind, now),
            host_permission=opening.host_permission,
            budget=opening.budget,
            created_at=now,
            updated_at=now,
        )

    def model_post_init(self, context: object, /) -> None:
        self._read_as = self.status

    @property
    def read_as(self) -> PauseStatus:
        """The status it was read with: what a fenced write expects to find."""
        return self._read_as

    def saved(self) -> None:
        self._read_as = self.status

    @property
    def decision(self) -> AgentRunApprovalDecision | None:
        return self.resolution.decision if self.resolution else None

    @property
    def response(self) -> dict[str, object]:
        return dict(self.resolution.response) if self.resolution else {}

    @property
    def call_args(self) -> dict[str, object]:
        args = self.call.tool_args if self.call is not None else None
        return dict(args) if isinstance(args, dict) else {}

    @property
    def parked(self) -> bool:
        """Held inside its own run, which a remote harness's bridge keeps going."""
        return self.call is not None and self.call.state.parked_in_run_id is not None

    @property
    def is_open(self) -> bool:
        return self.status is PauseStatus.PENDING and not self.returned

    # -- a person's pauses ------------------------------------------------------

    def resolve(
        self,
        decision: AgentRunApprovalDecision,
        response: dict[str, object] | None = None,
        *,
        by: UUID,
    ) -> bool:
        return self._end(
            PauseStatus.RESOLVED,
            PauseResolution(
                decision=decision, response=response or {}, resolved_by_user_id=by
            ),
        )

    def expire(self, now: datetime, *, on_behalf_of: UUID) -> bool:
        """Nobody decided in time: DENY, saying so, never an approval."""
        return self._end(
            PauseStatus.EXPIRED,
            PauseResolution(
                decision=AgentRunApprovalDecision.DENY,
                response={EXPIRED_UNANSWERED: True},
                resolved_by_user_id=on_behalf_of,
            ),
            now=now,
        )

    def answer_by_reply(self, reply: str, *, by: UUID) -> bool:
        """A question the person answered by writing back, not on its card.

        Their message is the answer, and reaches the model as one -- not as a
        question passed over. Only a question: an approval is never decided by
        a message that does not say which way.
        """
        if self.kind is not PauseKind.QUESTION:
            return False
        return self._end(
            PauseStatus.RESOLVED,
            PauseResolution(
                decision=AgentRunApprovalDecision.APPROVE_ONCE,
                response={"answers": {"reply": reply}, ANSWERED_BY_REPLY: True},
                resolved_by_user_id=by,
            ),
        )

    def supersede(self, *, by: UUID) -> bool:
        """The person moved past it: DENY on their behalf, never an approval."""
        return self._end(
            PauseStatus.SUPERSEDED,
            PauseResolution(
                decision=AgentRunApprovalDecision.DENY,
                response={SUPERSEDED_KEY: True},
                resolved_by_user_id=by,
            ),
        )

    def claim_execution(self) -> bool:
        """Whether this is an approval whose tool may now be run, once.

        The repository takes the claim fenced on ``execution_claimed_at``, so a
        retried worker never runs an approved tool a second time.
        """
        if (
            self.kind is not PauseKind.APPROVAL
            or self.status is not PauseStatus.RESOLVED
            or self.decision in (None, AgentRunApprovalDecision.DENY)
            or self.execution_claimed_at is not None
        ):
            return False
        self.execution_claimed_at = datetime.now(timezone.utc)
        return True

    # -- a wait -----------------------------------------------------------------

    def rearm(
        self, next_at: datetime, *, unreadable_since: datetime | None = None
    ) -> None:
        """Push a polled wait's next check out, without waking the agent.

        ``unreadable_since`` is carried while the target keeps not answering and
        cleared the first time it does, so only an unbroken run of failed reads
        wakes the agent with TARGET_UNREADABLE.
        """
        spec = self.wait_spec or WaitSpec()
        self.wake_at = next_at
        self.wait_spec = spec.model_copy(
            update={
                "poll_attempt": spec.poll_attempt + 1,
                "unreadable_since": unreadable_since,
            }
        )

    def wake(self, reason: AgentWaitWakeReason) -> bool:
        return self._end(
            PauseStatus.RESOLVED,
            PauseResolution(woke_because=reason),
            spec={"woke_because": reason},
        )

    def cancel(self) -> bool:
        """Ended undecided: a stopped wait, or a call its own return closed."""
        woke = AgentWaitWakeReason.CANCELLED if self.kind is PauseKind.WAIT else None
        return self._end(
            PauseStatus.CANCELLED,
            PauseResolution(woke_because=woke) if woke else None,
            spec={"woke_because": woke} if woke else None,
        )

    def abandon(self, error: str) -> bool:
        """Give up on a wait whose work item ran out of attempts.

        Not a wake: no return could be built, so there is nothing truthful to
        hand the model. The conversation stays WAITING; the error log is the
        operator's signal.
        """
        return self._end(PauseStatus.ABANDONED, None, spec={"abandoned_because": error})

    def _end(
        self,
        status: PauseStatus,
        resolution: PauseResolution | None,
        *,
        now: datetime | None = None,
        spec: dict[str, object] | None = None,
    ) -> bool:
        if self.status is not PauseStatus.PENDING:
            return False
        self.status = status
        self.resolution = resolution
        self.resolved_at = now or datetime.now(timezone.utc)
        if spec and self.wait_spec is not None:
            self.wait_spec = self.wait_spec.model_copy(update=spec)
        return True


__all__ = [
    "ALL_KINDS",
    "APPROVAL_EXPIRES_AFTER",
    "APPROVAL_KINDS",
    "ASKED_KINDS",
    "ASK_EXPIRES_AFTER",
    "CALL_NAMES",
    "DECIDED",
    "PAUSING_CALL_NAMES",
    "PERSON_KINDS",
    "Pause",
    "PauseResolution",
    "deadline_for",
]
