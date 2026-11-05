"""What a ``wait_for`` pause waits on, and what it recorded about itself.

The wait is a ``Pause`` of kind WAIT (``domain/pauses``); these are its own
fields' types. A conversation wait is *not* a schedule: a schedule is a standing
rule someone configured; this is one suspended run that resolves exactly once.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict


class AgentWaitType(str, Enum):
    """What the conversation is waiting on.

    Every member is armed with a work item due at ``scheduled_at``, whatever
    else resolves it: the timer is what guarantees the agent comes back at all, and a
    condition only resolves the wait sooner. For TIME the timer *is* the
    condition; for the others it is the deadline.

    How each is answered differs, and that is the only interesting difference:
    SUBAGENT is pushed (the child's finished run resolves it), while PROCESS
    is pulled, because nothing anywhere
    publishes a process exit -- the E2B watcher records it into Redis and tells
    nobody, and docker/lemma_local keep it inside the sandbox. A pulled wait
    re-arms its own item between checks, so the poll costs a Redis read on a
    worker rather than a model round trip on the agent's turn.

    Waking on a record change was scoped out deliberately: reacting to a row
    changing is what a DATASTORE trigger is for, and a second path to the same
    event is a duplication this codebase can do without.

    Human pauses are also absent: they are pauses of their own kinds, decided
    by a person rather than woken.
    """

    TIME = "TIME"
    PROCESS = "PROCESS"
    SUBAGENT = "SUBAGENT"


#: Wait types whose condition nobody publishes, so the claim has to look. These
#: re-arm between checks instead of waking; see ``AgentWaitType``.
POLLED_WAIT_TYPES = frozenset({AgentWaitType.PROCESS})


class AgentWaitWakeReason(str, Enum):
    """Why the agent woke. Handed back to the model in the tool return.

    ANSWERED is deliberately a *reason*, not a second :class:`AgentWaitType`.
    The wait is still a timer — armed with a work item due at ``scheduled_at``
    — and the timer stays the guarantee that the agent comes back at all. An
    answer landing only resolves that timer sooner. Modelling it as a wait type
    would mean a wait that resolves on two unrelated conditions, and every
    timer path would have to learn about the second one.
    """

    TIMER = "TIMER"
    CANCELLED = "CANCELLED"
    # Everything this conversation asked a person with `channel_post` has been
    # answered. Unlike TIMER this *does* say something happened.
    ANSWERED = "ANSWERED"
    # The thing being waited on reached a terminal state. Says only that it
    # ended -- an exit code of 1 finishes just as much as an exit code of 0.
    TARGET_FINISHED = "TARGET_FINISHED"
    # It cannot be looked at any more: the sandbox went away, the child
    # conversation was deleted. The outcome is unknown rather than bad.
    TARGET_GONE = "TARGET_GONE"
    # The ceiling ran out with the target still unfinished. Distinct from TIMER,
    # which is a TIME wait arriving exactly where it meant to.
    DEADLINE = "DEADLINE"
    # The target stopped to ask a question and will not go on until it is
    # answered: a sub-agent that paused on ask_user or request_approval. Not
    # finished, so not TARGET_FINISHED, and nobody else can see its question.
    TARGET_NEEDS_INPUT = "TARGET_NEEDS_INPUT"
    # The target could not be read for long enough that waiting on is waiting
    # blind: its sandbox did not answer. Says nothing about whether it ended.
    TARGET_UNREADABLE = "TARGET_UNREADABLE"


def _lenient(parse):
    """A spec field parsed when it can be, None when it cannot.

    The spec is stored JSON written by every release since waits existed. One
    unreadable field must not make the whole wait unreadable -- a wait that
    cannot be parsed cannot be woken, and an agent that is never woken is lost.
    """

    def validate(value: object) -> object:
        if value is None:
            return None
        try:
            return parse(value)
        except TypeError, ValueError:
            return None

    return BeforeValidator(validate)


def _as_datetime(value: object) -> datetime:
    parsed = (
        value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    )
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _as_uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


LenientDatetime = Annotated[datetime | None, _lenient(_as_datetime)]


class WaitSpec(BaseModel):
    """What a wait recorded about itself, typed.

    Stored as the row's JSON ``spec``. Unknown keys are kept rather than
    dropped, so a field an older or newer release wrote survives a re-arm.
    """

    model_config = ConfigDict(extra="allow")

    reason: str | None = None
    note_to_self: str | None = None
    started_at: LenientDatetime = None
    #: When the wait gives up. None means it cannot say, which reads as due.
    deadline_at: LenientDatetime = None
    target_ref: str | None = None
    #: The sub-agent runs a wait on several waits for, all of them; it wakes
    #: once every one has ended, or one stops to ask.
    target_refs: list[str] | None = None
    user_id: Annotated[UUID | None, _lenient(_as_uuid)] = None
    #: The unclamped asks, kept so the resolution can see what the model wanted.
    requested_seconds: int | None = None
    requested_max_seconds: int | None = None
    poll_attempt: int = 0
    #: When the target first stopped answering, cleared when it answers again.
    unreadable_since: LenientDatetime = None
    #: Why the wait resolved. A reason a later release no longer knows reads
    #: as unknown rather than making the wait unreadable.
    woke_because: Annotated[
        AgentWaitWakeReason | None, _lenient(AgentWaitWakeReason)
    ] = None
    #: Why its work item gave up on waking it.
    abandoned_because: str | None = None

    @property
    def targets(self) -> list[str]:
        """Every target this wait is on: its set, or its one."""
        if self.target_refs:
            return list(self.target_refs)
        return [self.target_ref] if self.target_ref else []

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
