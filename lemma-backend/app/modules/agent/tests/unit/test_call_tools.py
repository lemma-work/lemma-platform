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
from app.modules.agent.infrastructure.tools.call.models import RelayToVoiceRequest
from app.modules.agent.infrastructure.tools.call.tools import relay_to_voice
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.services.tool_catalog import ToolSurface, specs_for

pytestmark = pytest.mark.unit


def test_a_call_mark_round_trips_and_a_worker_inherits_the_call() -> None:
    mark = CallMark(call_id=uuid4(), role=CallRole.COORDINATOR)
    assert CallMark.from_json(mark.to_json()) == mark
    assert mark.for_worker() == CallMark(call_id=mark.call_id, role=CallRole.WORKER)
    assert CallMark.from_json({"call_id": "nope", "role": "coordinator"}) is None
    assert CallMark.from_json("x") is None
    assert CallMark.from_json(None) is None


async def test_only_the_coordinator_gets_the_voice_and_nothing_else_is_added() -> None:
    user_id, pod_id = uuid4(), uuid4()

    async def voice_tools(call: CallMark | None) -> set[str]:
        conversation = Conversation(user_id=user_id, pod_id=pod_id, call=call)
        ctx = ToolContext(
            user_id=user_id, pod_id=pod_id, conversation_id=conversation.id
        )
        specs = await specs_for(
            agent=None,
            conversation=conversation,
            ctx=ctx,
            surface=ToolSurface.IN_PROCESS,
            uow_factory=None,
        )
        return {spec.name for spec in specs} & {"relay_to_voice"}

    coordinator = CallMark(call_id=uuid4(), role=CallRole.COORDINATOR)
    assert await voice_tools(coordinator) == {"relay_to_voice"}
    # Its sub-agents show things with `display_resource` like any run.
    assert await voice_tools(coordinator.for_worker()) == set()
    assert await voice_tools(None) == set()


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
    ctx = SimpleNamespace(tool_call_id="call-1")
    said = await relay_to_voice(
        ctx, RelayToVoiceRequest(text="found three", urgency="when_idle")
    )  # type: ignore[arg-type]
    known = await relay_to_voice(
        ctx, RelayToVoiceRequest(text="started", urgency="context")
    )  # type: ignore[arg-type]
    assert said.success and "to be said" in (said.message or "")
    assert known.success and "for the voice to know" in (known.message or "")
