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
from datetime import datetime, timedelta, timezone

from app.modules.agent.domain.wait import (
    AgentConversationWaitEntity,
    AgentWaitType,
    AgentWaitWakeReason,
)


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


def decide_wait(
    wait: AgentConversationWaitEntity,
    *,
    target_reason: AgentWaitWakeReason | None,
    now: datetime,
    poll_delay_seconds: int,
) -> WaitDecision:
    """Resolve, re-arm, or give up, for a wait whose scheduled time arrived.

    ``target_reason`` is what reading the target established, or None for "still
    going" -- which is also what a TIME wait always reports, since it has no
    target to read.
    """
    if target_reason is not None:
        return WakeNow(target_reason)
    if wait.wait_type is AgentWaitType.TIME:
        # For TIME the timer *is* the condition: arriving here is the answer.
        return WakeNow(AgentWaitWakeReason.TIMER)

    deadline = deadline_of(wait, fallback=now)
    if now >= deadline:
        # Not TIMER. TIMER means "your time is up, as you asked"; DEADLINE means
        # "I gave up waiting and it had not finished", and the two imply
        # opposite things about whether to go and check.
        return WakeNow(AgentWaitWakeReason.DEADLINE)

    # Never past the ceiling: a wait with three seconds left must not sleep
    # sixty and overrun the limit it was given.
    return LookAgainAt(min(deadline, now + timedelta(seconds=poll_delay_seconds)))


def deadline_of(wait: AgentConversationWaitEntity, *, fallback: datetime) -> datetime:
    """When this wait gives up, from its spec; ``fallback`` if it cannot say.

    A row written before deadlines existed, or with an unparseable one, is not
    left waiting forever. Treating it as already due is the safe direction: an
    agent told its wait expired can check and carry on, while one that is never
    woken is simply lost.
    """
    raw = (wait.spec or {}).get("deadline_at")
    if not isinstance(raw, str):
        return fallback
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return fallback
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
