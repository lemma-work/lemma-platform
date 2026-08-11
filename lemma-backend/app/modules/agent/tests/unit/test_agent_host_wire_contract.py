"""The Python half of the wire contract in the Agent Host's shared fixture.

`agent-host/tests/wire_contract.rs` asserts the same file. Two things exist
twice across the two languages and nothing used to check that the copies agree:

* ``EventType`` / ``AgentHostEventType`` — a value one side emits and the other
  does not know is an event that arrives and is dropped;
* ``chunk_text`` / ``event_text`` — the host accumulates streamed text with one
  and this process re-accumulates it with the other, reconciling the two
  buffers at every segment boundary. A disagreement raises nothing. It
  silently truncates a persisted message, which is precisely how the
  seal-and-clear bug in ``_Segment`` stayed invisible.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.agent.domain.agent_host import AgentHostEventType
from app.modules.agent.infrastructure.harnesses.agent_host_events import event_text


def _contract() -> dict:
    """The fixture the Rust crate owns, read from its place in the repo.

    Deliberately not copied here: a second copy is the problem this file
    exists to prevent.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = (
            parent / "desktop" / "agent-host" / "tests" / "fixtures" / "wire_contract.json"
        )
        if candidate.exists():
            return json.loads(candidate.read_text())
    raise AssertionError(
        "desktop/agent-host/tests/fixtures/wire_contract.json was not found; the "
        "backend and the host must be checked out together to verify the wire "
        "contract they share"
    )


CONTRACT = _contract()


def test_the_event_type_enum_matches_the_contract() -> None:
    assert {member.value for member in AgentHostEventType} == set(
        CONTRACT["event_types"]
    ), (
        "an event one side emits and the other does not know is an event that "
        "reaches this process and is dropped"
    )


@pytest.mark.parametrize(
    "case",
    CONTRACT["text_extraction"],
    ids=[case["name"] for case in CONTRACT["text_extraction"]],
)
def test_event_text_matches_the_contract(case: dict) -> None:
    assert event_text(case["payload"]) == case["text"]
