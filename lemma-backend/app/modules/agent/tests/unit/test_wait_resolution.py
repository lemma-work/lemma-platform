"""What a fired wait *means*, which is the whole of what is new here.

The wake path was proven by the timer tool that came before this one. What is
new is that a fired timer is no longer automatically an answer: for a process or
a child run it is a prompt to go and look, and the three ways that can end --
it finished, it is still going, we ran out of patience -- are different things
to tell the agent.

The rule is a pure function of (wait, what the target said, now), so none of
this needs a database, a sandbox, a resumed run, or a double standing in front
of half the service that acts on it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.agent.domain.wait import (
    AgentConversationWaitEntity,
    AgentWaitType,
    AgentWaitWakeReason,
)
from app.modules.agent.domain.wait_decision import (
    LookAgainAt,
    WakeNow,
    deadline_of,
    decide_wait,
)
from app.modules.agent.tools.waiting.models import (
    POLL_CAP_SECONDS,
    POLL_START_SECONDS,
    next_poll_delay,
)

_NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


def _wait(wait_type: AgentWaitType, *, deadline_in: float = 3600, **spec):
    return AgentConversationWaitEntity(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        pod_id=uuid4(),
        tool_call_id="tc-1",
        wait_type=wait_type,
        scheduled_at=_NOW,
        spec={
            "started_at": _NOW.isoformat(),
            "deadline_at": (_NOW + timedelta(seconds=deadline_in)).isoformat(),
            "target_ref": "target-1",
            **spec,
        },
    )


def _decide(wait, *, target_reason=None, now=_NOW, poll_delay_seconds=10):
    return decide_wait(
        wait,
        target_reason=target_reason,
        now=now,
        poll_delay_seconds=poll_delay_seconds,
    )


def test_a_process_still_running_looks_again_rather_than_waking():
    """The normal answer for most checks of a long wait, and it must be silent.

    Waking here would replay the whole conversation to tell the agent nothing
    has happened, which is the cost this feature exists to remove.
    """
    decision = _decide(_wait(AgentWaitType.PROCESS))

    assert isinstance(decision, LookAgainAt)
    assert decision.next_at == _NOW + timedelta(seconds=10)


def test_a_finished_target_wakes_for_the_reason_the_target_gave():
    decision = _decide(
        _wait(AgentWaitType.PROCESS),
        target_reason=AgentWaitWakeReason.TARGET_FINISHED,
    )

    assert isinstance(decision, WakeNow)
    assert decision.reason is AgentWaitWakeReason.TARGET_FINISHED


def test_a_target_that_cannot_be_read_any_more_wakes_as_gone():
    decision = _decide(
        _wait(AgentWaitType.SUBAGENT),
        target_reason=AgentWaitWakeReason.TARGET_GONE,
    )

    assert isinstance(decision, WakeNow)
    assert decision.reason is AgentWaitWakeReason.TARGET_GONE


def test_a_process_past_its_ceiling_wakes_with_deadline_not_timer():
    """The two imply opposite things about whether to go and check.

    TIMER means "your time is up, as you asked". DEADLINE means "I stopped
    waiting and it had not finished" — an agent told TIMER for that would
    reasonably conclude the thing was done.
    """
    decision = _decide(_wait(AgentWaitType.PROCESS, deadline_in=-1))

    assert isinstance(decision, WakeNow)
    assert decision.reason is AgentWaitWakeReason.DEADLINE


def test_a_time_wait_wakes_on_its_timer_because_that_is_the_condition():
    decision = _decide(_wait(AgentWaitType.TIME))

    assert isinstance(decision, WakeNow)
    assert decision.reason is AgentWaitWakeReason.TIMER


def test_looking_again_never_pushes_past_the_ceiling():
    """Otherwise a wait with three seconds left would sleep sixty."""
    decision = _decide(
        _wait(AgentWaitType.PROCESS, deadline_in=3), poll_delay_seconds=60
    )

    assert isinstance(decision, LookAgainAt)
    assert decision.next_at == _NOW + timedelta(seconds=3)


@pytest.mark.parametrize("raw", ["not-a-date", None, 12345])
def test_a_wait_with_no_usable_deadline_is_treated_as_due(raw):
    """A row written before deadlines existed must not wait forever.

    Waking early is the safe direction: an agent told its wait expired can go
    and check, while one that is never woken is simply lost.
    """
    wait = _wait(AgentWaitType.PROCESS)
    wait.spec = {**wait.spec, "deadline_at": raw}

    assert deadline_of(wait, fallback=_NOW) == _NOW
    decided = _decide(wait)
    assert isinstance(decided, WakeNow)
    assert decided.reason is AgentWaitWakeReason.DEADLINE


def test_a_naive_deadline_is_read_as_utc():
    """Postgres hands back naive datetimes on some paths; a wait must not shift."""
    wait = _wait(AgentWaitType.PROCESS)
    wait.spec = {**wait.spec, "deadline_at": "2026-09-17T13:00:00"}

    assert deadline_of(wait, fallback=_NOW) == datetime(
        2026, 9, 17, 13, 0, tzinfo=timezone.utc
    )


def test_rearming_resets_the_failed_wake_counter():
    """A re-arm is a *successful* check, and the counter counts failed wakes.

    Without this a wait that checks every ten seconds for an hour crosses
    MAX_WAKE_ATTEMPTS on three unrelated transient errors spread across that
    hour, and is abandoned with its target perfectly healthy.
    """
    wait = _wait(AgentWaitType.PROCESS)
    wait.wake_attempts = 2

    wait.rearm(_NOW + timedelta(seconds=10))

    assert wait.wake_attempts == 0
    assert wait.spec["poll_attempt"] == 1


def test_the_poll_backs_off_to_a_cap():
    """Responsive while a fast finish is still plausible, then cheap."""
    assert next_poll_delay(0) == POLL_START_SECONDS
    assert next_poll_delay(1) == POLL_START_SECONDS * 2
    assert next_poll_delay(50) == POLL_CAP_SECONDS
