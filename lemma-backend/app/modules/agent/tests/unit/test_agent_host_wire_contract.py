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
from uuid import uuid7

from app.modules.agent.domain.agent_host import AgentHostEventType
from app.modules.agent.domain.value_objects import AgentEventType, MessageKind
from app.modules.agent.infrastructure.harnesses.agent_host_events import (
    AgentHostEventEnvelope,
    AgentHostEventNormalizer,
    event_text,
)


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


@pytest.mark.parametrize(
    "case",
    CONTRACT["tool_calls"],
    ids=[case["name"] for case in CONTRACT["tool_calls"]],
)
def test_a_tool_call_arrives_with_its_arguments_and_its_result(case: dict) -> None:
    """The other half of the tool-call contract.

    The host's half, asserted in ``wire_contract.rs``, is that nothing an
    adapter reported is dropped on the way here. This half is that what arrived
    is actually read: the arguments out of whichever field carried them, and an
    MCP result out of the envelope around it. Both halves are needed and
    neither is sufficient — the arguments did reach this process, in
    ``rawInput`` on a status-less update, and were thrown away on arrival.
    """
    normalizer = AgentHostEventNormalizer(agent_run_id=uuid7(), model_name="test")
    messages = [
        event
        for sequence, update in enumerate(case["updates"], start=1)
        for event in normalizer.normalize(
            AgentHostEventEnvelope(
                sequence=sequence,
                type=(
                    AgentHostEventType.TOOL_CALL_UPSERT.value
                    if update["sessionUpdate"] == "tool_call"
                    else AgentHostEventType.TOOL_CALL_UPDATE.value
                ),
                object_id=update.get("toolCallId"),
                payload={
                    key: value
                    for key, value in update.items()
                    if key not in {"sessionUpdate", "toolCallId"}
                },
            )
        )
        if event.type is AgentEventType.MESSAGE
    ]

    calls = [m for m in messages if m.data.kind is MessageKind.TOOL_CALL]
    returns = [m for m in messages if m.data.kind is MessageKind.TOOL_RETURN]
    assert len(calls) == 1, "one tool use must render as exactly one call"
    assert len(returns) == 1, "one tool use must render as exactly one return"
    assert calls[0].data.tool_args == case["tool_args"]
    assert returns[0].data.tool_result == case["tool_result"]
