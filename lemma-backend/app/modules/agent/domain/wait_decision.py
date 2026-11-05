"""What a fired wait means: wake for a reason, or look again later.

Pure, and separate from the service that acts on it, because this is the part
that is genuinely new. The wake path was proven by the timer that came before;
what is new is that a fired timer is no longer automatically an answer. For a
process or a child run it is a prompt to go and look, and the three ways that
can end are different things to tell the agent.

Keeping it a function of (wait, outcome, now) means the rules can be read and
tested without a database, a sandbox or a resumed run -- and without standing a
double in front of half the service to do it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.modules.agent.domain.pauses import Pause
from app.modules.agent.domain.wait import AgentWaitType, AgentWaitWakeReason


@dataclass(frozen=True, slots=True)
class WakeNow:
    """Resume the agent, and tell it this is why."""

    reason: AgentWaitWakeReason


@dataclass(frozen=True, slots=True)
class LookAgainAt:
    """Leave the agent waiting and re-arm the row for this time."""

    next_at: datetime


#: Two outcomes, as two types rather than one with both fields optional. The
#: optional version typechecked as "either may be None", so the caller had to
#: either assert or carry a `datetime | None` into a function that cannot take
#: one -- which is exactly what the typechecker caught.
WaitDecision = WakeNow | LookAgainAt


#: How long a target may go unanswered before the agent is told so. Long enough
#: to ride out a provider blip or a sandbox restarting; short enough that a
#: sandbox which is simply not coming back costs minutes rather than the whole
#: wait. Before this, the deadline was the only way out of an unreadable target.
UNREADABLE_GRACE_SECONDS = 5 * 60


def decide_wait(
    wait: Pause,
    *,
    target_reason: AgentWaitWakeReason | None,
    now: datetime,
    poll_delay_seconds: int,
    unreadable_since: datetime | None = None,
) -> WaitDecision:
    """Resolve, re-arm, or give up, for a wait whose scheduled time arrived.

    ``target_reason`` is what reading the target established, or None for "still
    going" -- which is also what a TIME wait always reports, since it has no
    target to read. ``unreadable_since`` is set when this read, and every read
    since that moment, could not reach the target at all.
    """
    if target_reason is not None:
        return WakeNow(target_reason)
    if wait.wait_type is AgentWaitType.TIME:
        # For TIME the timer *is* the condition: arriving here is the answer.
        return WakeNow(AgentWaitWakeReason.TIMER)

    unreadable_too_long = unreadable_since is not None and now - unreadable_since >= (
        timedelta(seconds=UNREADABLE_GRACE_SECONDS)
    )
    deadline = deadline_of(wait, fallback=now)
    if unreadable_too_long or (unreadable_since is not None and now >= deadline):
        # Not DEADLINE, which says the target was still running when last
        # looked at: nothing could be looked at, and the agent should know
        # that its wait was blind rather than waiting blind again.
        return WakeNow(AgentWaitWakeReason.TARGET_UNREADABLE)
    if now >= deadline:
        # Not TIMER. TIMER means "your time is up, as you asked"; DEADLINE means
        # "I gave up waiting and it had not finished", and the two imply
        # opposite things about whether to go and check.
        return WakeNow(AgentWaitWakeReason.DEADLINE)

    # Never past the ceiling: a wait with three seconds left must not sleep
    # sixty and overrun the limit it was given.
    return LookAgainAt(min(deadline, now + timedelta(seconds=poll_delay_seconds)))


def deadline_of(wait: Pause, *, fallback: datetime) -> datetime:
    """When this wait gives up, from its spec; ``fallback`` if it cannot say.

    A row written before deadlines existed, or with an unparseable one, is not
    left waiting forever. Treating it as already due is the safe direction: an
    agent told its wait expired can check and carry on, while one that is never
    woken is simply lost.
    """
    deadline = wait.wait_spec.deadline_at if wait.wait_spec else None
    return deadline or fallback
