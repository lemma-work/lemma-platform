"""The Python half of the wire contract in the Agent Host's shared fixture.

`agent-host/tests/wire_contract.rs` asserts the same file. Two things exist
twice across the two languages and nothing used to check that the copies agree:

* ``EventType`` / ``AgentHostEventType`` — a value one side emits and the other
  does not know is an event that arrives and is dropped;
* ``chunk_text`` / ``event_text`` — the host accumulates streamed text with one
  and this process re-accumulates it with the other, reconciling the two
  buffers at every segment boundary. A disagreement raises nothing. It
  silently truncates a persisted message, which is precisely how the
  seal-and-clear bug in ``Segment`` stayed invisible;
* the ``RunSpec`` field list — a field added on one side and not the other is
  either a spec the host silently ignores or one this process cannot see, and
  the fixture is also where ``mcp``'s deliberate absence from this side is
  written down so nobody closes the gap by declaring it here;
* the wire enums and the link's frames, close codes and error codes -- the
  host branches on every one of them, and a value only one side knows is a
  request nobody answers or a close the host misreads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.agent.domain.agent_host import (
    AGENT_HOST_PROTOCOL_VERSION,
    AgentHostCapacity,
    AgentHostCommandKind,
    AgentHostEvent,
    AgentHostEventType,
    AgentHostHarnessHealth,
    AgentHostRejectionCode,
    AgentHostRunSpec,
    AgentHostRunState,
    AgentHostStatus,
    AgentHostToolSource,
    AgentHostToolStatus,
)
from app.modules.agent.domain.agent_host_link import (
    AGENT_HOST_LINK_HEARTBEAT_MS,
    AGENT_HOST_LINK_PATH,
    LINK_CLOSE_CODES,
    HostFrameType,
    LinkErrorCode,
    ServerFrameType,
)
from app.modules.agent.infrastructure.harnesses.agent_host.events import (
    event_text,
)


def _contract() -> dict:
    """The fixture the Rust crate owns, read from its place in the repo.

    Deliberately not copied here: a second copy is the problem this file
    exists to prevent.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = (
            parent
            / "desktop"
            / "agent-host"
            / "tests"
            / "fixtures"
            / "wire_contract.json"
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


def test_the_declared_limits_are_the_ones_this_side_enforces() -> None:
    """The bounds the host is asked to respect must be the bounds we apply.

    Neither is a graceful degradation if it drifts. An ``object_id`` longer than
    the column refuses the whole batch, which the host reads as the run's own
    fault and answers by discarding the transcript; a ``max_runs`` above the cap
    makes every poll 422, so a paired computer reports itself offline
    indefinitely. Both limits used to live only in this file's field
    definitions, where the host could not see them.
    """
    limits = _contract()["limits"]

    object_id = AgentHostEvent.model_fields["object_id"]
    declared = next(
        item.max_length
        for item in object_id.metadata
        if getattr(item, "max_length", None) is not None
    )
    assert declared == limits["object_id_max_length"]

    max_runs = AgentHostCapacity.model_fields["max_runs"]
    ceiling = next(
        item.le for item in max_runs.metadata if getattr(item, "le", None) is not None
    )
    assert ceiling == limits["max_runs"]


def test_the_protocol_version_is_the_one_the_host_sends() -> None:
    """One number, in two languages, that nothing used to tie together.

    The host puts ``PROTOCOL_VERSION`` in every identity it publishes and this
    process compares it. Raising one side and not the other makes every host of
    the old version look unrecognised, from the moment this deploys, with
    nothing failing on either side to say so.
    """
    assert AGENT_HOST_PROTOCOL_VERSION == _contract()["protocol_version"]


def test_the_run_spec_declares_the_fields_the_contract_names() -> None:
    """The run spec is one payload in two languages, and this side omits a field.

    ``mcp`` is on the wire -- ``_wire_command`` decrypts ``encrypted_mcp`` into
    it as the command is handed to the host -- but it is deliberately not a
    field of this model. A model field is a place the plaintext could be
    persisted back into the command row, which is the one thing encrypting it
    at rest exists to prevent. Every other field is declared on both sides, and
    nothing tied the two lists together: adding a field here and not in
    ``protocol.rs`` gives the host a spec it silently ignores, and adding one
    there and not here makes it a field this side cannot see.
    """
    run_spec = _contract()["run_spec"]
    shared = set(run_spec["fields"])
    on_delivery = set(run_spec["added_on_delivery"])

    assert set(AgentHostRunSpec.model_fields) == shared
    assert shared.isdisjoint(on_delivery)
    for field in on_delivery:
        assert field not in AgentHostRunSpec.model_fields, (
            f"{field} is added when the command is delivered; declaring it here "
            f"would let the plaintext be persisted with the command"
        )


_ENUMS = {
    "run_state": AgentHostRunState,
    "host_status": AgentHostStatus,
    "harness_health": AgentHostHarnessHealth,
    "rejection_code": AgentHostRejectionCode,
    "command_kind": AgentHostCommandKind,
    "tool_source": AgentHostToolSource,
    "tool_status": AgentHostToolStatus,
}


@pytest.mark.parametrize("name", sorted(_ENUMS))
def test_each_wire_enum_matches_the_contract(name: str) -> None:
    """Compared as sets, in both directions.

    A value only the host knows is one this process rejects when it arrives; a
    value only this process knows is one the host drops when it is sent.
    """
    assert {member.value for member in _ENUMS[name]} == set(CONTRACT["enums"][name])


def test_every_contract_enum_is_asserted_here() -> None:
    """A new enum in the fixture must not go unchecked on this side."""
    assert set(CONTRACT["enums"]) == set(_ENUMS)


def test_the_link_frames_are_the_ones_the_host_speaks() -> None:
    """A frame one side sends and the other does not know is never answered."""
    link = CONTRACT["link"]
    assert {member.value for member in HostFrameType} == set(link["host_frames"])
    assert {member.value for member in ServerFrameType} == set(link["server_frames"])


def test_the_link_close_codes_are_the_ones_the_host_branches_on() -> None:
    """The host's whole vocabulary for why a socket closed.

    Getting one wrong is not cosmetic: three 4401s in a row make the host drop
    its pairing, and a 4426 stops it until Desktop updates it.
    """
    assert LINK_CLOSE_CODES == CONTRACT["link"]["close_codes"]


def test_the_link_error_codes_are_the_ones_the_host_acts_on() -> None:
    assert {member.value for member in LinkErrorCode} == set(
        CONTRACT["link"]["error_codes"]
    )


def test_the_link_path_and_heartbeat_are_shared() -> None:
    link = CONTRACT["link"]
    assert AGENT_HOST_LINK_PATH == link["path"]
    assert AGENT_HOST_LINK_HEARTBEAT_MS == link["heartbeat_ms"]
