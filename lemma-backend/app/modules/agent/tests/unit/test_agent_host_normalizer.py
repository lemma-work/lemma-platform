"""Normalizer behaviour on a single ordered event lane.

Several of these pin defects found reviewing the two-lane design: a stream key
leaking into message metadata, every flush claiming to be the final answer, and
an upsert re-emitting text the user had already seen.
"""

from __future__ import annotations

import json

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


class TestStructuredFinalAnswer:
    """How a structured result gets back out of an ACP run.

    ACP tool-call events carry no tool *name* — `ToolCall` has a `title` the
    agent wrote and a `toolCallId`, and nothing else — so the normalizer
    recognises our final answer by the marker the tool stamps into its own
    result, wherever the adapter happens to echo it.
    """

    SCHEMA = {
        "type": "object",
        "properties": {"label": {"type": "string"}},
        "required": ["label"],
    }

    def _structured(self, **kwargs) -> AgentHostEventNormalizer:
        return AgentHostEventNormalizer(
            agent_run_id=uuid7(),
            model_name="test-model",
            harness_key="codex",
            structured_expected=True,
            **kwargs,
        )

    def _close_tool_call(self, payload: dict) -> list:
        return [
            _event(
                1,
                AgentHostEventType.TOOL_CALL_UPSERT,
                {"toolCall": {"title": "Finish up"}},
                object_id="call-1",
            ),
            _event(
                2,
                AgentHostEventType.TOOL_CALL_UPDATE,
                {"status": "completed", **payload},
                object_id="call-1",
            ),
        ]

    def _final_metadata(self, out) -> dict:
        finals = [
            m.data.metadata
            for m in _text_messages(out)
            if m.data.metadata.get("is_final_answer")
        ]
        assert len(finals) == 1
        return finals[0]

    RECORD = {
        "lemma_final_answer": True,
        "status": "COMPLETED",
        "output": {"label": "spam"},
        "error": None,
    }

    def test_result_payload_becomes_structured_output(self) -> None:
        n = self._structured()
        out = _run(n, self._close_tool_call({"result": self.RECORD}))

        metadata = self._final_metadata(out)
        assert metadata["structured_output"] == {"label": "spam"}
        assert metadata["final_answer_status"] == "COMPLETED"
        assert metadata["tool_call_id"] == "call-1"

    def test_arguments_are_read_when_the_adapter_reports_no_output(self) -> None:
        """Some adapters echo rawInput but not rawOutput; the args are the answer."""
        n = self._structured()
        out = _run(n, self._close_tool_call({"rawInput": self.RECORD}))

        assert self._final_metadata(out)["structured_output"] == {"label": "spam"}

    def test_text_only_result_is_recognised(self) -> None:
        """Others echo only the MCP text block, which carries the same marker."""
        n = self._structured()
        out = _run(n, self._close_tool_call({"text": json.dumps(self.RECORD)}))

        assert self._final_metadata(out)["structured_output"] == {"label": "spam"}

    def test_a_large_nested_output_is_not_mangled_by_bounding(self) -> None:
        """The record is read raw. `_bounded_tool_value` replaces anything past
        its depth limit with a placeholder, which would leave `output_data`
        looking structured while being nothing of the sort."""
        deep = {"a": {"b": {"c": {"d": {"e": "kept"}}}}}
        n = self._structured()
        out = _run(
            n,
            self._close_tool_call(
                {"result": {**self.RECORD, "output": {"label": "x", "deep": deep}}}
            ),
        )

        assert self._final_metadata(out)["structured_output"]["deep"] == deep

    def test_the_last_call_wins(self) -> None:
        n = self._structured()
        events = [
            *self._close_tool_call({"result": self.RECORD}),
            _event(
                3,
                AgentHostEventType.TOOL_CALL_UPSERT,
                {"toolCall": {"title": "Actually"}},
                object_id="call-2",
            ),
            _event(
                4,
                AgentHostEventType.TOOL_CALL_UPDATE,
                {
                    "status": "completed",
                    "result": {**self.RECORD, "output": {"label": "ham"}},
                },
                object_id="call-2",
            ),
        ]
        out = _run(n, events)

        assert self._final_metadata(out)["structured_output"] == {"label": "ham"}

    def test_a_permission_pause_does_not_burn_the_answer(self) -> None:
        """A mid-run pause flushes with final=False; the record must survive to
        the terminal flush or a run that pauses loses its result."""
        n = self._structured()
        events = [
            *self._close_tool_call({"result": self.RECORD}),
            _event(
                3,
                AgentHostEventType.PERMISSION_REQUEST,
                {"toolCall": {"toolCallId": "perm-1"}},
                object_id="perm-1",
            ),
        ]
        out = _run(n, events)

        assert self._final_metadata(out)["structured_output"] == {"label": "spam"}

    def test_a_recorded_answer_overrides_what_the_stream_inferred(self) -> None:
        """The tool's own record is the authority; the stream is a heuristic."""
        n = self._structured()
        for event in self._close_tool_call({"result": self.RECORD}):
            n.normalize(event)
        n.adopt_final_answer({**self.RECORD, "output": {"label": "authoritative"}})
        out = n.normalize(
            _event(9, AgentHostEventType.TERMINAL, {"state": "SUCCEEDED"})
        )

        assert self._final_metadata(out)["structured_output"] == {
            "label": "authoritative"
        }


class TestFinalAnswerTextFallback:
    """Reading the answer out of the agent's own prose, when it never called the
    tool. This is a guess, so it is fenced hard: the whole message must be the
    JSON, and it must satisfy the agent's schema."""

    SCHEMA = TestStructuredFinalAnswer.SCHEMA

    def _normalizer(self, *, expected: bool = True, schema=None):
        return AgentHostEventNormalizer(
            agent_run_id=uuid7(),
            model_name="test-model",
            harness_key="codex",
            structured_expected=expected,
            output_schema=schema,
        )

    def _say(self, normalizer, text: str) -> list:
        return _run(
            normalizer,
            [_event(1, AgentHostEventType.AGENT_MESSAGE_UPSERT, {"text": text})],
        )

    def _metadata(self, out) -> dict:
        finals = [
            m.data.metadata
            for m in _text_messages(out)
            if m.data.metadata.get("is_final_answer")
        ]
        return finals[0] if finals else {}

    def test_a_whole_message_contract_object_is_accepted(self) -> None:
        out = self._say(
            self._normalizer(schema=self.SCHEMA),
            json.dumps({"status": "COMPLETED", "output": {"label": "spam"}}),
        )

        metadata = self._metadata(out)
        assert metadata["structured_output"] == {"label": "spam"}
        assert metadata["final_answer_status"] == "COMPLETED"
        # Flagged, so "the tool path is failing on adapter X" is visible in data.
        assert metadata["final_answer_inferred"] is True

    def test_a_fenced_block_that_is_the_whole_message_is_accepted(self) -> None:
        out = self._say(
            self._normalizer(schema=self.SCHEMA),
            '```json\n{"status": "COMPLETED", "output": {"label": "spam"}}\n```',
        )

        assert self._metadata(out)["structured_output"] == {"label": "spam"}

    def test_a_bare_object_becomes_the_output_with_no_invented_status(self) -> None:
        """Never synthesize a lifecycle status from a guess — let the terminal
        event decide whether the run succeeded."""
        out = self._say(self._normalizer(schema=self.SCHEMA), '{"label": "spam"}')

        metadata = self._metadata(out)
        assert metadata["structured_output"] == {"label": "spam"}
        assert "final_answer_status" not in metadata

    def test_json_quoted_inside_prose_is_rejected(self) -> None:
        """The false-positive that matters: an agent explaining a payload has not
        produced a final answer. A brace-slice of the text would take it."""
        out = self._say(
            self._normalizer(schema=self.SCHEMA),
            'Here is the shape I would return: {"label": "spam"} — sound right?',
        )

        assert "structured_output" not in self._metadata(out)

    def test_output_violating_the_schema_is_rejected(self) -> None:
        out = self._say(self._normalizer(schema=self.SCHEMA), '{"score": 1}')

        assert "structured_output" not in self._metadata(out)

    def test_an_ordinary_chat_run_is_never_scraped(self) -> None:
        """No structured answer was owed, so JSON in a reply stays just text."""
        out = self._say(
            self._normalizer(expected=False, schema=None), '{"label": "spam"}'
        )

        assert "structured_output" not in self._metadata(out)
