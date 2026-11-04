"""A run in a live call: who is in it, and who gets the voice."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.calls import (
    CANVAS_TOOL_NAMES,
    CallMark,
    CallRole,
    call_mark,
    coordinator_brief,
    worker_canvas_note,
)
from app.modules.agent.tools.call.models import RelayToVoiceRequest
from app.modules.agent.tools.call.pydantic_adapter import relay_to_voice
from app.modules.agent.tools.tool_assembler import _call_toolsets

pytestmark = pytest.mark.unit


def test_a_call_mark_round_trips_and_a_worker_inherits_the_call() -> None:
    from datetime import UTC, datetime

    mark = CallMark(
        call_id=uuid4(),
        role=CallRole.COORDINATOR,
        live=True,
        since=datetime(2026, 10, 26, tzinfo=UTC),
        held_seconds=42.5,
    )
    assert call_mark({"call": mark.as_metadata()}) == mark
    assert mark.for_worker().role is CallRole.WORKER
    assert mark.for_worker().call_id == mark.call_id
    # A mark from before ``live`` existed is not a held call.
    legacy = {"call": {"call_id": str(mark.call_id), "role": "coordinator"}}
    assert call_mark(legacy).live is False  # type: ignore[union-attr]
    assert call_mark({"call": {"call_id": "nope", "role": "coordinator"}}) is None
    assert call_mark({"call": "x"}) is None
    assert call_mark(None) is None


def test_only_the_coordinator_gets_the_voice_and_nothing_else_is_added() -> None:
    def conversation(metadata: dict[str, object] | None) -> SimpleNamespace:
        return SimpleNamespace(metadata=metadata)

    call_id = uuid4()
    coordinator = CallMark(call_id=call_id, role=CallRole.COORDINATOR, live=True)
    [toolset] = _call_toolsets(conversation({"call": coordinator.as_metadata()}))
    assert set(toolset.tools) == {"relay_to_voice"}  # type: ignore[attr-defined]
    # Once the call is over, the chat has no voice to relay to.
    ended = CallMark(call_id=call_id, role=CallRole.COORDINATOR, live=False)
    assert _call_toolsets(conversation({"call": ended.as_metadata()})) == []
    # Its sub-agents show things with `display_resource` like any run.
    assert (
        _call_toolsets(conversation({"call": coordinator.for_worker().as_metadata()}))
        == []
    )
    assert _call_toolsets(conversation({})) == []
    assert _call_toolsets(None) == []


def test_the_canvas_is_what_runs_already_show() -> None:
    assert CANVAS_TOOL_NAMES == {"display_resource"}
    # Both briefs point at the one way of showing things, and at the `show`
    # widgets for visuals given as data.
    for brief in (coordinator_brief(), worker_canvas_note()):
        assert "display_resource" in brief and "fallback" in brief
        assert "show_on_canvas" not in brief
    assert "/skills/lemma-widget/library/show/" in coordinator_brief()


def test_the_coordinator_is_briefed_for_the_call_only_while_it_is_live() -> None:
    """In the run's context, so a call can be held in any chat and the chat
    reads as itself again once the call ends."""
    from app.modules.agent.services.brief_lines import call_brief

    call_id = uuid4()

    def chat(live: bool, role: CallRole = CallRole.COORDINATOR) -> SimpleNamespace:
        mark = CallMark(call_id=call_id, role=role, live=live)
        return SimpleNamespace(metadata={"call": mark.as_metadata()})

    brief = call_brief(chat(live=True))
    assert brief.startswith("## You are on a live voice call")
    # Every turn gets a status relay, so the voice never assumes work.
    assert "for EVERY" in brief and "relay_to_voice" in brief
    assert call_brief(chat(live=False)) == ""
    assert call_brief(chat(live=True, role=CallRole.WORKER)) == ""
    assert call_brief(SimpleNamespace(metadata=None)) == ""


async def test_a_relay_is_acknowledged_as_recorded_not_as_said() -> None:
    ctx = SimpleNamespace(tool_call_id="call-1", deps=SimpleNamespace())
    said = await relay_to_voice(
        ctx, RelayToVoiceRequest(text="found three", urgency="when_idle")
    )  # type: ignore[arg-type]
    known = await relay_to_voice(
        ctx, RelayToVoiceRequest(text="started", urgency="context")
    )  # type: ignore[arg-type]
    assert said.success and "to be said" in (said.message or "")
    assert known.success and "for the voice to know" in (known.message or "")
