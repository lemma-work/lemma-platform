"""Which finished agent runs a schedule hears about, and as what."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.schedule.work.agent_outcome import started_by_schedule

pytestmark = pytest.mark.unit


def _fact(origin: str, conversation_status: str) -> AgentRunFinished:
    return AgentRunFinished(
        run_id=uuid4(),
        conversation_id=uuid4(),
        pod_id=uuid4(),
        status="COMPLETED",
        conversation_status=conversation_status,  # type: ignore[arg-type]
        origin=origin,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED", "STOPPED"])
def test_a_finished_schedule_conversation_is_recorded(status: str) -> None:
    assert started_by_schedule(_fact("schedule", status))


def test_a_run_that_paused_is_not_the_conversation_finishing() -> None:
    assert not started_by_schedule(_fact("schedule", "WAITING"))


def test_conversations_nobody_scheduled_are_not_the_schedules() -> None:
    assert not started_by_schedule(_fact("chat", "COMPLETED"))
    assert not started_by_schedule(_fact("workflow", "COMPLETED"))
