"""A run must never leave a tool call open when it ends.

A run that dies between a tool executing and its result being recorded leaves a
call with no return. The next run rebuilds history, finds the orphan, and
`pydantic_ai_history._build_tool_batch` synthesizes "This tool call was
interrupted before a result was recorded... Run it again if you still need the
result." So a send that *succeeded* is described to the model as never having
happened, and the model is told to do it again -- a duplicate email, a duplicate
record write, a destructive command run twice.

`runtime_history._is_unpaired_tool_call` documents this hazard precisely and
guards the one place it could arise from elision. The harness that actually
produces the orphans did not.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
    MessageKind,
)
from app.modules.agent.infrastructure.harnesses.pydantic_ai import PydanticAIHarness
from app.modules.agent.infrastructure.harnesses.tool_returns import (
    OutstandingToolCalls,
)

pytestmark = pytest.mark.unit

RUN_ID = UUID("00000000-0000-0000-0000-0000000000ff")


def _call(tool_call_id: str, tool_name: str = "send_email") -> AgentEvent:
    return AgentEvent(
        type=AgentEventType.MESSAGE,
        data=MessageDraft.of_tool_call(
            tool_name=tool_name, tool_call_id=tool_call_id, tool_args={}
        ),
        agent_run_id=RUN_ID,
    )


def _return(tool_call_id: str, tool_name: str = "send_email") -> AgentEvent:
    return AgentEvent(
        type=AgentEventType.MESSAGE,
        data=MessageDraft.of_tool_return(
            tool_name=tool_name, tool_call_id=tool_call_id, tool_result={"ok": True}
        ),
        agent_run_id=RUN_ID,
    )


def _terminal(kind: AgentEventType = AgentEventType.ERROR) -> AgentEvent:
    return AgentEvent(type=kind, data="boom", agent_run_id=RUN_ID)


class TestTheTracker:
    def test_an_answered_call_leaves_nothing_open(self) -> None:
        tracker = OutstandingToolCalls()
        tracker.observe(_call("c1"))
        tracker.observe(_return("c1"))

        assert tracker.closing_events(_terminal()) == []

    def test_an_unanswered_call_is_closed(self) -> None:
        tracker = OutstandingToolCalls()
        tracker.observe(_call("c1"))

        closing = tracker.closing_events(_terminal())

        assert len(closing) == 1
        assert closing[0].data.tool_call_id == "c1"
        assert closing[0].data.kind is MessageKind.TOOL_RETURN

    def test_the_close_says_the_tool_did_not_return(self) -> None:
        """Not 'it failed' -- the tool may well have succeeded. What is known is
        that no result was recorded."""
        tracker = OutstandingToolCalls()
        tracker.observe(_call("c1"))

        result = tracker.closing_events(_terminal())[0].data.tool_result

        assert result["success"] is False
        assert "before the tool returned a result" in result["error"]

    def test_closing_twice_does_not_emit_twice(self) -> None:
        tracker = OutstandingToolCalls()
        tracker.observe(_call("c1"))
        tracker.closing_events(_terminal())

        assert tracker.closing_events(_terminal()) == []

    def test_the_pausing_call_is_left_for_the_user_to_answer(self) -> None:
        """`ask_user` gets its real return when the user replies; closing it here
        would duplicate that."""
        tracker = OutstandingToolCalls()
        tracker.observe(_call("pause1", "ask_user"))
        tracker.observe(_call("c2", "exec_command"))

        closing = tracker.closing_events(
            _terminal(AgentEventType.WAITING), skip_tool_call_id="pause1"
        )

        assert [event.data.tool_call_id for event in closing] == ["c2"]

    def test_only_message_events_are_tracked(self) -> None:
        tracker = OutstandingToolCalls()
        tracker.observe(
            AgentEvent(type=AgentEventType.TOKEN, data="hi", agent_run_id=RUN_ID)
        )

        assert tracker.closing_events(_terminal()) == []


class TestTheHarnessClosesWhatItOpened:
    async def _events(self, monkeypatch, fake_execute) -> list[AgentEvent]:
        harness = PydanticAIHarness()
        monkeypatch.setattr(harness, "_execute", fake_execute)
        return [
            event
            async for event in harness.run(
                agent=SimpleNamespace(),
                conversation=SimpleNamespace(id=RUN_ID),
                messages=[],
                ctx=SimpleNamespace(),
                options=SimpleNamespace(should_stop=None),
                agent_run_id=RUN_ID,
            )
        ]

    async def test_a_call_open_when_the_provider_fails_is_closed(
        self, monkeypatch
    ) -> None:
        async def fake_execute(**kwargs):
            yield _call("c1")
            raise ModelHTTPError(status_code=500, model_name="m", body={})

        events = await self._events(monkeypatch, fake_execute)

        returns = [
            event
            for event in events
            if isinstance(event.data, MessageDraft)
            and event.data.kind is MessageKind.TOOL_RETURN
        ]
        assert [event.data.tool_call_id for event in returns] == ["c1"]

    async def test_the_close_lands_before_the_run_ends(self, monkeypatch) -> None:
        """It has to be persisted as part of this run, or the next run rebuilds
        the same orphan."""

        async def fake_execute(**kwargs):
            yield _call("c1")
            raise ModelHTTPError(status_code=500, model_name="m", body={})

        events = await self._events(monkeypatch, fake_execute)

        kinds = [
            "return"
            if isinstance(event.data, MessageDraft)
            and event.data.kind is MessageKind.TOOL_RETURN
            else event.type.value
            for event in events
        ]
        assert kinds[-2:] == ["return", AgentEventType.ERROR.value]

    async def test_an_answered_call_is_not_closed_again(self, monkeypatch) -> None:
        async def fake_execute(**kwargs):
            yield _call("c1")
            yield _return("c1")
            raise ModelHTTPError(status_code=500, model_name="m", body={})

        events = await self._events(monkeypatch, fake_execute)

        returns = [
            event
            for event in events
            if isinstance(event.data, MessageDraft)
            and event.data.kind is MessageKind.TOOL_RETURN
        ]
        assert len(returns) == 1

    async def test_a_clean_run_closes_nothing(self, monkeypatch) -> None:
        async def fake_execute(**kwargs):
            yield _call("c1")
            yield _return("c1")

        events = await self._events(monkeypatch, fake_execute)

        assert events[-1].type is AgentEventType.COMPLETED
        assert len(events) == 3

    async def test_a_run_that_completes_with_a_call_still_open_closes_it(
        self, monkeypatch
    ) -> None:
        """`end_strategy="graceful"` lets a sibling tool end the response, so a
        batch can finish with one member unanswered."""

        async def fake_execute(**kwargs):
            yield _call("c1")

        events = await self._events(monkeypatch, fake_execute)

        returns = [
            event
            for event in events
            if isinstance(event.data, MessageDraft)
            and event.data.kind is MessageKind.TOOL_RETURN
        ]
        assert [event.data.tool_call_id for event in returns] == ["c1"]
        assert events[-1].type is AgentEventType.COMPLETED


class TestAStoppedRunClosesWhatItOpened:
    """The case every test above missed: a terminal event that is *yielded*.

    Every other path builds its terminal after the stream ends, so the closing
    returns naturally precede it. A stop does not -- STOPPED comes out of the
    stream itself. It used to be forwarded the moment it appeared and the
    closing returns emitted after it, and `RunEventPump` drops everything once
    it has seen a terminal event. So a stopped run kept a tool call that
    nothing ever answered, and the next run could reasonably repeat a tool that
    had already run: exactly the duplicate-send the closing returns exist to
    prevent.
    """

    async def _events(self, monkeypatch, fake_execute) -> list[AgentEvent]:
        harness = PydanticAIHarness()
        monkeypatch.setattr(harness, "_execute", fake_execute)
        return [
            event
            async for event in harness.run(
                agent=SimpleNamespace(),
                conversation=SimpleNamespace(id=RUN_ID),
                messages=[],
                ctx=SimpleNamespace(),
                options=SimpleNamespace(should_stop=None),
                agent_run_id=RUN_ID,
            )
        ]

    async def test_a_stop_closes_the_open_call_before_it_ends_the_run(
        self, monkeypatch
    ) -> None:
        async def fake_execute(**kwargs):
            yield _call("c1")
            yield AgentEvent(
                type=AgentEventType.STOPPED,
                data={},
                agent_run_id=RUN_ID,
            )

        events = await self._events(monkeypatch, fake_execute)

        kinds = [
            "return"
            if isinstance(event.data, MessageDraft)
            and event.data.kind is MessageKind.TOOL_RETURN
            else event.type.value
            for event in events
        ]
        # Not `kinds[-2:]`: with the terminal forwarded early *and* re-emitted
        # at the end, the last two entries still read return, stopped while the
        # pump had already stopped listening. The stop has to appear once, and
        # every close has to be in front of it.
        assert kinds.count(AgentEventType.STOPPED.value) == 1
        stop_at = kinds.index(AgentEventType.STOPPED.value)
        assert stop_at == len(kinds) - 1
        assert "return" in kinds[:stop_at]

    async def test_the_stop_is_emitted_exactly_once(self, monkeypatch) -> None:
        """Holding the terminal back must not also duplicate it."""

        async def fake_execute(**kwargs):
            yield _call("c1")
            yield AgentEvent(
                type=AgentEventType.STOPPED,
                data={},
                agent_run_id=RUN_ID,
            )

        events = await self._events(monkeypatch, fake_execute)

        assert [e.type for e in events].count(AgentEventType.STOPPED) == 1
