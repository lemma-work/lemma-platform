"""Where a completed run says it happened, and when analytics has to go and look.

`agent_run.completed` is conversation-scoped, so the pod, organization, agent and
person behind it are either carried on the event or read back. The event carries
them now; the read is the fallback for the two paths that finish a run without a
live run context -- the stop-request handler and the status sweeps -- and for
events published before the fields existed.

Both halves are pinned here because the failure mode is silent in each direction:
a fallback that never fires looks identical to one that always does, and the only
difference is a database round-trip per completed agent run. So the assertion is
about the unit of work, which is the thing that costs something.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.agent.contracts.conversations import ConversationScope
from app.modules.agent.domain.events import AgentRunCompletedEvent
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.analytics.events.agent import _run_scope
from app.modules.analytics.services.buckets import duration_seconds, seconds_bucket


class _WentToTheDatabase(Exception):
    """Raised by the stand-in factory the moment a lookup is attempted."""


class _RecordingUowFactory:
    """A unit of work nobody should be able to open without this test noticing."""

    def __call__(self):
        raise _WentToTheDatabase


def _event(**fields) -> AgentRunCompletedEvent:
    return AgentRunCompletedEvent(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        status=AgentRunStatus.COMPLETED,
        **fields,
    )


async def test_an_enriched_event_is_answered_without_touching_the_database() -> None:
    pod_id, organization_id, agent_id, user_id = (uuid4() for _ in range(4))
    scope = await _run_scope(
        _RecordingUowFactory(),
        _event(
            pod_id=pod_id,
            organization_id=organization_id,
            agent_id=agent_id,
            user_id=user_id,
        ),
    )
    assert scope == ConversationScope(
        user_id=user_id,
        pod_id=pod_id,
        organization_id=organization_id,
        agent_id=agent_id,
    )


async def test_an_event_without_the_fields_is_looked_up() -> None:
    """A run ended by the stop handler or a status sweep carries no pod, and it
    still has to be measured."""
    with pytest.raises(_WentToTheDatabase):
        await _run_scope(_RecordingUowFactory(), _event())


@pytest.mark.parametrize("present", ["pod_id", "user_id"])
async def test_a_half_filled_event_is_looked_up_rather_than_guessed_at(
    present: str,
) -> None:
    """Both fields are needed to answer from the event. Emitting a run with no
    pod, or crediting it to nobody, is worse than the round-trip."""
    with pytest.raises(_WentToTheDatabase):
        await _run_scope(_RecordingUowFactory(), _event(**{present: uuid4()}))


def test_the_run_duration_is_measured_from_the_run_not_the_conversation() -> None:
    """`started_at` rides on the event for this reason alone. It used to be the
    conversation's creation time -- the only start the consumer could see -- which
    reported the age of the thread on every turn after the first."""
    started = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    event = _event(started_at=started, occurred_at=started + timedelta(seconds=3))
    assert seconds_bucket(duration_seconds(event.started_at, event.occurred_at)) == (
        "1-5s"
    )


def test_a_run_with_no_recorded_start_reports_no_duration_at_all() -> None:
    """The sweeps know when they noticed an abandoned run, not when it began.
    Reporting the gap between those two would be a made-up number in a band
    people read off a dashboard."""
    event = _event()
    assert seconds_bucket(duration_seconds(event.started_at, event.occurred_at)) is None
