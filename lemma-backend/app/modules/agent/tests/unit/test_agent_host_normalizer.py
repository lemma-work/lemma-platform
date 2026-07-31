"""Normalizer behaviour on a single ordered event lane.

Several of these pin defects found reviewing the two-lane design: a stream key
leaking into message metadata, every flush claiming to be the final answer, and
an upsert re-emitting text the user had already seen.
"""

from __future__ import annotations

from uuid import uuid7

from app.modules.agent.domain.agent_host import AgentHostEventType, AgentHostRunState
from app.modules.agent.domain.value_objects import AgentEventType
from app.modules.agent.infrastructure.harnesses.agent_host_events import (
    AgentHostEventEnvelope,
    AgentHostEventNormalizer,
    is_terminal_event,
)


def _normalizer() -> AgentHostEventNormalizer:
    return AgentHostEventNormalizer(
        agent_run_id=uuid7(),
        model_name="test-model",
        harness_key="codex",
    )


def _event(
    sequence: int,
    event_type: AgentHostEventType,
    payload: dict,
    *,
    object_id: str | None = None,
) -> AgentHostEventEnvelope:
    return AgentHostEventEnvelope(
        sequence=sequence,
        type=event_type.value,
        object_id=object_id,
        payload=payload,
    )


def _tokens(events) -> str:
    return "".join(
        e.data["data"] for e in events if e.type is AgentEventType.TOKEN
    )


def _messages(events):
    return [e for e in events if e.type is AgentEventType.MESSAGE]


def _text_messages(events):
    """Assistant text/thinking messages only — never a synthesized tool call."""
    return [e for e in _messages(events) if e.data.tool_call_id is None]


def _run(normalizer, events) -> list:
    """Feed events and terminate, so buffered tokens are drained."""
    out = []
    for event in events:
        out += normalizer.normalize(event)
    out += normalizer.normalize(
        _event(len(events) + 1, AgentHostEventType.TERMINAL, {"state": "SUCCEEDED"})
    )
    return out


def _final_text(events) -> str:
    return "".join(
        m.data.text
        for m in _messages(events)
        if m.data.text and m.data.metadata.get("is_final_answer")
    )


class TestTextAccumulation:
    def test_chunks_accumulate_into_one_message(self) -> None:
        n = _normalizer()
        out = _run(
            n,
            [
                _event(i, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": text})
                for i, text in enumerate(["Hel", "lo ", "world"], start=1)
            ],
        )
        assert _tokens(out) == "Hello world"
        assert _final_text(out) == "Hello world"

    def test_upsert_emits_only_the_new_tail(self) -> None:
        """An upsert supersedes what already streamed; re-emitting the whole
        segment would show the user duplicated text."""
        n = _normalizer()
        out = _run(
            n,
            [
                _event(1, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "Hel"}),
                _event(2, AgentHostEventType.AGENT_MESSAGE_UPSERT, {"text": "Hello"}),
            ],
        )
        assert _tokens(out) == "Hello"
        assert _final_text(out) == "Hello"

    def test_upsert_that_does_not_extend_emits_no_duplicate(self) -> None:
        """Ordered delivery means this should not happen; if it somehow does,
        re-emitting the full text would duplicate what the user already saw."""
        n = _normalizer()
        out = _run(
            n,
            [
                _event(1, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "Hello"}),
                _event(
                    2, AgentHostEventType.AGENT_MESSAGE_UPSERT, {"text": "Different"}
                ),
            ],
        )
        assert _tokens(out) == "Hello"
        assert _final_text(out) == "Different"


class TestFinalAnswerFlag:
    def test_only_the_terminal_flush_is_the_final_answer(self) -> None:
        """A run that pauses for permission and then completes must not emit
        two messages both claiming to be the final answer."""
        n = _normalizer()
        n.normalize(
            _event(1, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "thinking..."})
        )
        paused = n.normalize(
            _event(2, AgentHostEventType.PERMISSION_REQUEST, {"tool": "bash"})
        )
        n.normalize(
            _event(3, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "done"})
        )
        finished = n.normalize(
            _event(4, AgentHostEventType.TERMINAL, {"state": "SUCCEEDED"})
        )

        paused_flags = [
            m.data.metadata.get("is_final_answer") for m in _text_messages(paused)
        ]
        final_flags = [
            m.data.metadata.get("is_final_answer") for m in _text_messages(finished)
        ]
        assert paused_flags == [False]
        assert final_flags == [True]


class TestMetadata:
    def test_message_metadata_carries_the_host_object_id(self) -> None:
        """This used to leak the internal stream key instead."""
        n = _normalizer()
        n.normalize(
            _event(
                1,
                AgentHostEventType.AGENT_MESSAGE_CHUNK,
                {"text": "hi"},
                object_id="msg-42",
            )
        )
        out = n.normalize(
            _event(2, AgentHostEventType.TERMINAL, {"state": "SUCCEEDED"})
        )
        ids = [m.data.metadata.get("agent_host_object_id") for m in _messages(out)]
        assert ids == ["msg-42"]
        assert "agent-message" not in str(ids)


class TestToolCalls:
    def test_tool_call_and_return_are_emitted_once(self) -> None:
        n = _normalizer()
        opened = n.normalize(
            _event(
                1,
                AgentHostEventType.TOOL_CALL_UPSERT,
                {"name": "read_file"},
                object_id="call-1",
            )
        )
        duplicate = n.normalize(
            _event(
                2,
                AgentHostEventType.TOOL_CALL_UPSERT,
                {"name": "read_file"},
                object_id="call-1",
            )
        )
        closed = n.normalize(
            _event(
                3,
                AgentHostEventType.TOOL_CALL_UPDATE,
                {"status": "COMPLETED", "result": "ok"},
                object_id="call-1",
            )
        )
        assert len(_messages(opened)) == 1
        assert duplicate == []
        assert len(_messages(closed)) == 1

    def test_unfinished_tool_calls_are_closed_at_terminal(self) -> None:
        n = _normalizer()
        n.normalize(
            _event(
                1,
                AgentHostEventType.TOOL_CALL_UPSERT,
                {"name": "read_file"},
                object_id="call-1",
            )
        )
        out = n.normalize(
            _event(2, AgentHostEventType.TERMINAL, {"state": "FAILED"})
        )
        assert any(m for m in _messages(out))
        assert out[-1].type is AgentEventType.ERROR


class TestTerminalMapping:
    def test_succeeded_maps_to_completed(self) -> None:
        n = _normalizer()
        out = n.normalize(
            _event(1, AgentHostEventType.TERMINAL, {"state": "SUCCEEDED"})
        )
        assert out[-1].type is AgentEventType.COMPLETED

    def test_cancelled_maps_to_stopped(self) -> None:
        n = _normalizer()
        out = n.normalize(
            _event(1, AgentHostEventType.TERMINAL, {"state": "CANCELLED"})
        )
        assert out[-1].type is AgentEventType.STOPPED

    def test_waiting_input_maps_to_waiting(self) -> None:
        n = _normalizer()
        out = n.normalize(
            _event(1, AgentHostEventType.TERMINAL, {"state": "WAITING_INPUT"})
        )
        assert out[-1].type is AgentEventType.WAITING

    def test_missing_terminal_event_still_ends_the_run(self) -> None:
        n = _normalizer()
        out = n.finish_without_terminal(state=AgentHostRunState.SUCCEEDED)
        assert out[-1].type is AgentEventType.ERROR


class TestPermissionRequest:
    def test_permission_request_becomes_a_request_approval_call(self) -> None:
        """The pause is rendered as an ordinary Lemma approval, so every client
        that already knows how to show one needs no Agent Host special case."""
        n = _normalizer()
        out = n.normalize(
            _event(
                1,
                AgentHostEventType.PERMISSION_REQUEST,
                {
                    "toolCall": {"toolCallId": "perm-1", "title": "Run rm -rf build"},
                    "options": [{"optionId": "allow", "kind": "allow_once"}],
                },
                object_id="perm-1",
            )
        )

        calls = [m for m in _messages(out) if m.data.tool_call_id is not None]
        assert [m.data.tool_name for m in calls] == ["request_approval"]
        assert calls[0].data.tool_call_id == "agent-host-permission:perm-1"
        assert calls[0].data.tool_args["title"] == "Run rm -rf build"
        assert not any(e.type is AgentEventType.ERROR for e in out)

    def test_permission_request_does_not_end_the_run(self) -> None:
        """WAITING terminates a run. The host holds this request open *inside* a
        run that is still going, so emitting WAITING would strand everything the
        agent does after the decision."""
        n = _normalizer()
        out = n.normalize(
            _event(
                1,
                AgentHostEventType.PERMISSION_REQUEST,
                {"toolCall": {"toolCallId": "perm-1"}},
                object_id="perm-1",
            )
        )

        assert not any(is_terminal_event(e) for e in out)
        assert not any(e.type is AgentEventType.WAITING for e in out)

    def test_permission_status_carries_what_a_surface_needs_to_render(self) -> None:
        """The STATUS event is the only pause signal left, so it must carry the
        approval's identity or Slack/Teams/Telegram render nothing."""
        n = _normalizer()
        out = n.normalize(
            _event(
                1,
                AgentHostEventType.PERMISSION_REQUEST,
                {"toolCall": {"toolCallId": "perm-1"}},
                object_id="perm-1",
            )
        )

        statuses = [e.data for e in out if e.type is AgentEventType.STATUS]
        assert [s["status"] for s in statuses] == ["permission_request"]
        assert statuses[0]["kind"] == "request_approval"
        assert statuses[0]["tool_call_id"] == "agent-host-permission:perm-1"
