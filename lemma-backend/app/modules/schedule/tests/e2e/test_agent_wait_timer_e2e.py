"""An agent's TIME wait still fires through the schedule poller.

TIME schedules and workflow timers are work items now, but agent waits keep
their own table until PR 3 moves them onto ``agent.wait.fire``: the poller
claims a due wait (``claim_due_waits``), stages its fire, and the
``workflow-schedule-events`` consumer resolves the conversation wait. This
drives that whole path through the real worker.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.modules.agent.domain.state_kinds import PauseStatus
from app.modules.agent.domain.wait import AgentWaitType
from app.modules.agent.tests.e2e.test_wait_resolution_e2e import (
    _a_wait,
    _reload,
    waiting_conversation,  # noqa: F401  fixture
)
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_a_due_agent_time_wait_is_fired_by_the_poller(
    waiting_conversation,  # noqa: F811
    worker,
):
    _ = worker
    wait = await _a_wait(
        waiting_conversation,
        wait_type=AgentWaitType.TIME,
        target_ref="",
        scheduled_in=timedelta(seconds=-1),
    )

    async def status() -> PauseStatus | None:
        current = await _reload(wait.id)
        return current.status if current is not None else None

    resolved = await eventually(
        label="agent TIME wait resolved by the poller",
        probe=status,
        done=lambda value: value is not None and value != PauseStatus.PENDING,
        timeout_seconds=60,
    )
    assert resolved == PauseStatus.RESOLVED
