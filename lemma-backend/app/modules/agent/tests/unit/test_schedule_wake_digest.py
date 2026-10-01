"""A triage digest wakes an agent knowing it holds many events, not one."""

from __future__ import annotations

import pytest

from app.modules.agent.infrastructure.adapters.workflow_control import (
    AgentControlAdapter,
)

pytestmark = pytest.mark.unit


def _digest(*, held: int, more_waiting: bool) -> dict[str, object]:
    return {
        "payload": {
            "events": [{"subject": f"Update {n}"} for n in range(held)],
            "held": held,
        },
        "metadata": {
            "schedule_name": "support-inbox",
            "trigger_type": "WEBHOOK",
            "fired_at": "2026-10-01T09:00:00+00:00",
            "digest": True,
            "held": held,
            "more_waiting": more_waiting,
        },
        "llm_output": {},
    }


def test_a_digest_says_it_is_one_and_where_its_events_are():
    prompt = AgentControlAdapter._schedule_wake_prompt(
        _digest(held=3, more_waiting=False), "Summarise what came in."
    )

    assert (
        "This run is a digest: 3 events the schedule held since its last digest, "
        "oldest first, under `payload.events`."
    ) in prompt
    assert "More are still held" not in prompt
    assert "Update 2" in prompt
    # Said once as a sentence, not repeated as event metadata.
    assert '"more_waiting"' not in prompt
    assert '"digest"' not in prompt


def test_a_digest_with_more_held_says_so():
    prompt = AgentControlAdapter._schedule_wake_prompt(
        _digest(held=1, more_waiting=True), None
    )

    assert "This run is a digest: 1 event the schedule held" in prompt
    assert "More are still held for the next digest." in prompt


def test_an_ordinary_firing_says_nothing_about_digests():
    prompt = AgentControlAdapter._schedule_wake_prompt(
        {
            "payload": {"subject": "Down again"},
            "metadata": {"schedule_name": "support-inbox", "trigger_type": "WEBHOOK"},
            "llm_output": {},
        },
        None,
    )

    assert "digest" not in prompt
