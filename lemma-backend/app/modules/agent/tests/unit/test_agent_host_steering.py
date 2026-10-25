"""Talking to a local coding agent while it works.

A message sent mid-turn waits in the conversation until something delivers it.
For an Agent Host harness that advertised ACP steering, the consume loop hands
it to the host while the turn is still running; for one that did not, the loop
never looks, and the follow-up turn answers it. What happens end to end -- the
host, the agent, the follow-up turn -- is in ``test_agent_host_steer_e2e.py``;
these pin the rules that decide it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid7

from app.modules.agent.domain.message_state import MessageState
from app.modules.agent.domain.value_objects import QueueState
from app.modules.agent.infrastructure.harnesses.agent_host.steering import (
    SteerSchedule,
    supports_steering,
)


class TestWhenARunLooks:
    def test_a_steerable_run_looks_at_once_and_then_on_its_interval(self) -> None:
        schedule = SteerSchedule(enabled=True, interval=1.0, now=10.0)

        assert schedule.due(10.0)
        assert not schedule.due(10.5)
        assert schedule.due(11.0)
        assert not schedule.due(11.2)

    def test_a_run_that_cannot_steer_never_looks(self) -> None:
        """Its host would not know the command. The follow-up turn answers."""
        schedule = SteerSchedule(enabled=False, interval=0.0, now=0.0)

        assert not any(schedule.due(float(second)) for second in range(10))


def test_steering_is_read_from_the_published_capabilities() -> None:
    assert supports_steering({"load_session": True, "steering": True})
    assert not supports_steering({"load_session": True})
    assert not supports_steering({})


class TestWithdrawing:
    """Until something is carrying it, a queued message can be taken back."""

    def test_a_message_nobody_has_read_is_queued_and_withdrawable(self) -> None:
        state = MessageState(queue_state=QueueState.QUEUED)
        assert state.is_queued
        assert state.is_withdrawable

    def test_a_delivered_message_is_neither(self) -> None:
        state = MessageState(
            queue_state=QueueState.STEERED, steered_into_run_id=uuid7()
        )
        assert not state.is_queued
        assert not state.is_withdrawable

    def test_a_steer_on_its_way_to_the_host_cannot_be_taken_back(self) -> None:
        """It may already be in the agent's context."""
        state = MessageState(queue_state=QueueState.QUEUED, steer_dispatched_at=_NOW)
        assert state.is_queued
        assert not state.is_withdrawable

    def test_a_steer_the_host_could_not_land_is_merely_queued_again(self) -> None:
        state = MessageState(
            queue_state=QueueState.STEER_UNDELIVERED, steer_dispatched_at=_NOW
        )
        assert state.is_queued
        assert state.is_withdrawable

    def test_one_held_behind_a_question_is_still_owed_an_answer(self) -> None:
        state = MessageState(queue_state=QueueState.WAITING_ON_ANSWER)
        assert state.is_queued
        assert state.is_withdrawable

    def test_a_message_that_started_its_own_turn_was_never_queued(self) -> None:
        assert not MessageState().is_queued
        assert not MessageState().is_withdrawable


_NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)
