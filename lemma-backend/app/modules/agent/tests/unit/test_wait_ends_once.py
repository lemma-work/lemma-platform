"""A wait ends once; a second ending changes nothing.

A Stop that lands just after the wake it raced used to overwrite the finished
wait: COMPLETED became CANCELLED, ``woke_because`` and ``completed_at`` were
rewritten, and the row no longer said what the history it wrote did.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

import pytest

from app.modules.agent.domain.wait import (
    AgentConversationWaitEntity,
    AgentWaitStatus,
    AgentWaitWakeReason,
)

pytestmark = pytest.mark.unit

Ending = Callable[[AgentConversationWaitEntity], bool]

_ENDINGS: dict[str, Ending] = {
    "complete": lambda wait: wait.complete(AgentWaitWakeReason.TIMER),
    "cancel": lambda wait: wait.cancel(),
    "abandon": lambda wait: wait.abandon("wake failed"),
}


def _wait() -> AgentConversationWaitEntity:
    return AgentConversationWaitEntity(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        pod_id=uuid4(),
        tool_call_id="tc-1",
    )


@pytest.mark.parametrize("first", list(_ENDINGS))
@pytest.mark.parametrize("second", list(_ENDINGS))
def test_a_finished_wait_keeps_its_first_ending(first: str, second: str) -> None:
    wait = _wait()
    assert _ENDINGS[first](wait) is True
    ended = (wait.status, dict(wait.spec), wait.completed_at)

    assert _ENDINGS[second](wait) is False
    assert (wait.status, wait.spec, wait.completed_at) == ended


def test_each_ending_records_why() -> None:
    completed, cancelled, abandoned = _wait(), _wait(), _wait()

    completed.complete(AgentWaitWakeReason.TIMER)
    cancelled.cancel()
    abandoned.abandon("wake failed")

    assert completed.status is AgentWaitStatus.COMPLETED
    assert completed.spec["woke_because"] == AgentWaitWakeReason.TIMER.value
    assert cancelled.status is AgentWaitStatus.CANCELLED
    assert cancelled.spec["woke_because"] == AgentWaitWakeReason.CANCELLED.value
    assert abandoned.status is AgentWaitStatus.FAILED
    assert abandoned.spec["abandoned_because"] == "wake failed"
