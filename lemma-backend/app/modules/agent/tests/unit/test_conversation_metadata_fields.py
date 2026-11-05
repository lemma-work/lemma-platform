"""Stored JSON the agent decides on, read typed and leniently."""

from __future__ import annotations

import pytest

from app.modules.agent.domain.wait import AgentWaitWakeReason, WaitSpec

pytestmark = pytest.mark.unit


def test_a_wait_reads_its_wake_reason_as_the_enum() -> None:
    assert (
        WaitSpec.model_validate({"woke_because": "TARGET_FINISHED"}).woke_because
        is AgentWaitWakeReason.TARGET_FINISHED
    )
    # A reason this release does not know does not make the wait unreadable.
    assert (
        WaitSpec.model_validate({"woke_because": "SOMETHING_NEW"}).woke_because is None
    )
    # And the stored shape is unchanged.
    assert (
        WaitSpec(woke_because=AgentWaitWakeReason.TIMER).to_json()["woke_because"]
        == "TIMER"
    )
