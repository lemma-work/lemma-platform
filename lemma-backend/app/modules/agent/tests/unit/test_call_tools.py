"""A run in a live call: who is in it, and who gets the voice."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.calls import (
    CANVAS_TOOL_NAMES,
    CallMark,
    CallRole,
    coordinator_brief,
    worker_canvas_note,
)
from app.modules.agent.tools.call.models import RelayToVoiceRequest
from app.modules.agent.tools.call.pydantic_adapter import relay_to_voice
from app.modules.agent.tools.tool_assembler import _call_toolsets

pytestmark = pytest.mark.unit


def test_a_call_mark_round_trips_and_a_worker_inherits_the_call() -> None:
    mark = CallMark(call_id=uuid4(), role=CallRole.COORDINATOR)
    assert CallMark.from_json(mark.to_json()) == mark
    assert mark.for_worker() == CallMark(call_id=mark.call_id, role=CallRole.WORKER)
    assert CallMark.from_json({"call_id": "nope", "role": "coordinator"}) is None
    assert CallMark.from_json("x") is None
    assert CallMark.from_json(None) is None


def test_only_the_coordinator_gets_the_voice_and_nothing_else_is_added() -> None:
    def conversation(call: CallMark | None) -> SimpleNamespace:
        return SimpleNamespace(call=call)

    call_id = uuid4()
    coordinator = CallMark(call_id=call_id, role=CallRole.COORDINATOR)
    [toolset] = _call_toolsets(conversation(coordinator))
    assert set(toolset.tools) == {"relay_to_voice"}  # type: ignore[attr-defined]
    # Its sub-agents show things with `display_resource` like any run.
    assert _call_toolsets(conversation(coordinator.for_worker())) == []
    assert _call_toolsets(conversation(None)) == []
    assert _call_toolsets(None) == []


def test_the_canvas_is_what_runs_already_show() -> None:
    assert CANVAS_TOOL_NAMES == {"display_resource"}
    # Both briefs point at the one way of showing things, and at the `show`
    # widgets for visuals given as data.
    for brief in (coordinator_brief(agent_display_name="Ria"), worker_canvas_note()):
        assert "display_resource" in brief and "fallback" in brief
        assert "show_on_canvas" not in brief
    assert "/skills/lemma-widget/library/show/" in coordinator_brief(
        agent_display_name="Ria"
    )


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
