"""Normalizer behaviour on a single ordered event lane.

Several of these pin defects found reviewing the two-lane design: a stream key
leaking into message metadata, every flush claiming to be the final answer, and
an upsert re-emitting text the user had already seen.

Tool calls here are protocol-3 events, already named and shaped by the host
(docs/architecture/agent-host-events.md). How one maps to its messages is in
``test_agent_host_tool_shapes``; this file is about the conversation around
them -- narration, thought, the final answer, permissions and run end.
"""

from __future__ import annotations

import json

from uuid import uuid7

from app.modules.agent.domain.agent_host import AgentHostEventType, AgentHostRunState
from app.modules.agent.domain.value_objects import AgentEventType, MessageKind
from app.modules.agent.infrastructure.harnesses.agent_host.events import (
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


def _call(
    sequence: int,
    call_id: str,
    name: str,
    tool_input: object = None,
    *,
    source: str = "native",
    title: str | None = None,
) -> AgentHostEventEnvelope:
    return _event(
        sequence,
        AgentHostEventType.TOOL_CALL,
        {
            "tool": {"name": name, "source": source, "title": title},
            "input": tool_input if tool_input is not None else {},
        },
        object_id=call_id,
    )


def _result(
    sequence: int,
    call_id: str,
    output: object = "ok",
    *,
    status: str = "completed",
    error: str | None = None,
) -> AgentHostEventEnvelope:
    return _event(
        sequence,
        AgentHostEventType.TOOL_CALL_RESULT,
        {"status": status, "output": output, "error": error},
        object_id=call_id,
    )


def _permission(
    sequence: int, call_id: str, name: str = "exec_command", title: str = "Run it"
) -> AgentHostEventEnvelope:
    return _event(
        sequence,
        AgentHostEventType.PERMISSION_REQUEST,
        {
            "request_id": call_id,
            "tool_call_id": call_id,
            "tool": {"name": name, "source": "native", "title": title},
            "input": {},
            "options": [{"option_id": "allow", "name": "Allow", "kind": "allow_once"}],
        },
        object_id=call_id,
    )


def _tokens(events) -> str:
    """Text and thinking as streamed; a tool's live indicator is not text."""
    return "".join(
        e.data["data"]
        for e in events
        if e.type is AgentEventType.TOKEN and e.data["kind"] in {"text", "thinking"}
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


def _intermediate_flags(events) -> list:
    """Whether each assistant text message is narration, in order."""
    return [
        bool((message.data.metadata or {}).get("is_intermediate_assistant_message"))
        for message in _text_messages(events)
        if message.data.kind is MessageKind.TEXT
    ]


def _persisted_text(events) -> str:
    """Every assistant text message run together, to compare against the stream."""
    return "".join(
        message.data.text
        for message in _text_messages(events)
        if message.data.kind is MessageKind.TEXT and message.data.text
    )


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

    def test_text_either_side_of_a_tool_call_is_all_kept(self) -> None:
        """The host seals and *clears* its buffer before every non-chunk event,
        so a message containing a tool call arrives as several upserts, each
        carrying only the piece since the last one.

        Treating each as the authoritative whole meant the persisted message
        held only the text after the final tool call. Nobody saw it: the chunks
        had already streamed the full text to the screen, so the loss showed up
        on reload and in the history the next turn was given.
        """
        n = _normalizer()
        out = _run(
            n,
            [
                _event(1, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "Hello"}),
                _event(2, AgentHostEventType.AGENT_MESSAGE_UPSERT, {"text": "Hello"}),
                _call(3, "c1", "read_file", {"file_path": "a.md"}),
                _result(4, "c1"),
                _event(5, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "! there"}),
                _event(6, AgentHostEventType.AGENT_MESSAGE_UPSERT, {"text": "! there"}),
            ],
        )
        # Both halves survive, and in order -- which is what this test has
        # always been for. What changed is that they are no longer one message:
        # the text before a tool call is sealed as its own, so nothing is glued
        # to what comes after it.
        assert _tokens(out) == "Hello! there"
        assert _persisted_text(out) == "Hello! there", (
            "what is persisted must match what the user watched stream"
        )
        said = [message.data.text for message in _text_messages(out)]
        assert said == ["Hello", "! there"], (
            "each thing the agent said is its own message"
        )
        assert _intermediate_flags(out) == [True, False], (
            "only the last one is the answer; the rest fold into the run"
        )

    def test_narration_between_tools_is_not_glued_into_one_paragraph(self) -> None:
        """The failure this shape produced, in the words it produced it in.

        A fifty-eight step run rendered as one paragraph reading "Loading
        schemas first.Schemas loaded. Starting the test sweep.Empty pod" -- every
        narration concatenated, with the report welded onto the end. The old
        fixture here was "Hello" + "! there", which reads perfectly when glued,
        so the assertion that pinned the concatenation never showed what it was
        pinning.
        """
        n = _normalizer()
        out = _run(
            n,
            [
                _event(
                    1,
                    AgentHostEventType.AGENT_MESSAGE_UPSERT,
                    {"text": "Loading schemas first."},
                ),
                _call(2, "c1", "load_skill", source="lemma"),
                _result(3, "c1"),
                _event(
                    4,
                    AgentHostEventType.AGENT_MESSAGE_UPSERT,
                    {"text": "Schemas loaded. Starting the sweep."},
                ),
            ],
        )

        said = [message.data.text for message in _text_messages(out)]
        assert said == ["Loading schemas first.", "Schemas loaded. Starting the sweep."]
        assert not any("first.Schemas" in text for text in said), (
            "two sentences from different messages ran together"
        )

    def test_a_segment_no_chunk_delivered_still_streams(self) -> None:
        """The host seals rich content ahead of itself, so an upsert can carry
        text the chunk lane never sent."""
        n = _normalizer()
        out = _run(
            n,
            [_event(1, AgentHostEventType.AGENT_MESSAGE_UPSERT, {"text": "Recovered"})],
        )
        assert _tokens(out) == "Recovered"
        assert _final_text(out) == "Recovered"

    def test_a_disagreeing_upsert_does_not_retract_what_was_streamed(self) -> None:
        """A token stream cannot take back what it emitted, so the host's
        record wins for the persisted text and nothing is re-streamed."""
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


class TestAnImageInsideAReply:
    """An image the agent produced mid-reply stays in the saved message.

    The harness saves an image block as a pod file and hands the normalizer a
    ``payload_override`` whose text is the markdown pointing at it. The host
    reads the same block as no text at all, so its upsert does not contain the
    markdown. With the markdown appended to the pending text, the upsert no
    longer started with what had streamed, and the host's text replaced it:
    the image showed while streaming and was gone on reload.
    """

    IMAGE = "![Generated image](agent-output/chart.png)"

    def _image_chunk(self, sequence: int) -> tuple[AgentHostEventEnvelope, dict]:
        raw = {"content": {"type": "image", "data": "aGk=", "mimeType": "image/png"}}
        return (
            _event(sequence, AgentHostEventType.AGENT_MESSAGE_CHUNK, raw),
            {**raw, "text": self.IMAGE},
        )

    def _reply(self, n: AgentHostEventNormalizer) -> list:
        image, override = self._image_chunk(2)
        out = n.normalize(
            _event(1, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "Here: "})
        )
        out += n.normalize(image, payload_override=override)
        out += n.normalize(
            _event(3, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": " done."})
        )
        # What the host seals: its own text, with nothing where the image was.
        out += n.normalize(
            _event(4, AgentHostEventType.AGENT_MESSAGE_UPSERT, {"text": "Here:  done."})
        )
        return out

    def test_the_saved_message_keeps_the_image_where_it_arrived(self) -> None:
        n = _normalizer()
        out = self._reply(n)
        out += n.normalize(
            _event(5, AgentHostEventType.TERMINAL, {"state": "SUCCEEDED"})
        )

        assert _final_text(out) == f"Here: {self.IMAGE} done."
        assert _persisted_text(out) == _tokens(out), (
            "what is saved must be what the user watched stream"
        )

    def test_the_image_survives_a_tool_call_sealing_the_segment(self) -> None:
        n = _normalizer()
        out = self._reply(n)
        out += _run(n, [_call(5, "c1", "read_file"), _result(6, "c1")])

        said = [m.data.text for m in _text_messages(out)]
        assert said == [f"Here: {self.IMAGE} done."]

    def test_an_unsealed_image_is_kept_at_the_end_of_the_turn(self) -> None:
        n = _normalizer()
        image, override = self._image_chunk(1)
        n.normalize(image, payload_override=override)
        out = n.normalize(
            _event(2, AgentHostEventType.TERMINAL, {"state": "SUCCEEDED"})
        )

        assert _final_text(out) == self.IMAGE


class TestThoughtPerStep:
    """Reasoning is saved before each step, not once for the whole run.

    Thought was flushed only at the end, so a run showed one "Thought" after
    all of its tool calls, holding every step's reasoning glued together.
    """

    def test_each_tool_call_gets_the_thought_that_led_to_it(self) -> None:
        n = _normalizer()
        out = _run(
            n,
            [
                _event(1, AgentHostEventType.AGENT_THOUGHT_UPSERT, {"text": "First."}),
                _call(2, "c1", "read_file"),
                _result(3, "c1"),
                _event(4, AgentHostEventType.AGENT_THOUGHT_UPSERT, {"text": "Second."}),
                _call(5, "c2", "grep"),
                _result(6, "c2"),
                _event(7, AgentHostEventType.AGENT_MESSAGE_UPSERT, {"text": "Done."}),
            ],
        )

        sequence = [
            m.data.text if m.data.kind is MessageKind.THINKING else m.data.tool_call_id
            for m in _messages(out)
            if m.data.kind in {MessageKind.THINKING, MessageKind.TOOL_CALL}
        ]
        assert sequence == ["First.", "c1", "Second.", "c2"]

    def test_a_permission_request_flushes_the_thought_before_it(self) -> None:
        n = _normalizer()
        n.normalize(
            _event(1, AgentHostEventType.AGENT_THOUGHT_UPSERT, {"text": "Risky."})
        )
        out = n.normalize(_permission(2, "perm-1"))

        kinds = [m.data.kind for m in _messages(out)]
        assert kinds[0] is MessageKind.THINKING
        assert _messages(out)[0].data.text == "Risky."
        assert MessageKind.TOOL_CALL in kinds


class TestFinalAnswerFlag:
    def test_only_the_terminal_flush_is_the_final_answer(self) -> None:
        """A run that pauses for permission and then completes must not emit
        two messages both claiming to be the final answer."""
        n = _normalizer()
        n.normalize(
            _event(1, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "thinking..."})
        )
        paused = n.normalize(_permission(2, "perm-1"))
        n.normalize(_event(3, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "done"}))
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

    def test_a_run_refused_before_it_started_ends_on_the_recorded_reason(
        self,
    ) -> None:
        """The only sentence such a run ever produces, so it has to be the one.

        The Agent Host fences a START_RUN naming a harness configuration the
        machine has replaced, and it does so before journaling anything: no
        ACCEPTED, no output, no terminal event. The lease terminalizes from the
        rejection receipt alone, and this used to surface as "Agent Host
        reached terminal checkpoint FAILED without its required terminal
        event" -- which names neither the agent nor the cause.
        """
        n = _normalizer()
        out = n.finish_without_terminal(
            state=AgentHostRunState.FAILED,
            detail=(
                "That computer and Lemma disagree about how Claude Code is "
                "configured; try sending again in a moment"
            ),
        )
        assert out[-1].type is AgentEventType.ERROR
        assert "Claude Code" in out[-1].data
        assert "terminal event" not in out[-1].data


class TestPermissionRequest:
    def test_the_approval_follows_the_call_it_gates_and_names_it(self) -> None:
        """The host releases the gated call before the request, so the card
        comes after the call it asks about and says the same word for it."""
        n = _normalizer()
        opening = n.normalize(
            _call(1, "read-project", "read_file", {"file_path": "README.md"})
        )
        permission = n.normalize(
            _permission(2, "read-project", name="read", title="Read README.md")
        )

        calls = [
            m
            for m in _messages([*opening, *permission])
            if m.data.tool_args is not None
        ]
        assert [call.data.tool_call_id for call in calls] == [
            "read-project",
            "agent-host-permission:read-project",
        ]
        assert calls[0].data.tool_args == {"file_path": "README.md"}
        # The name the call was announced under wins over the request's own.
        assert calls[1].data.tool_args["tool_name"] == "read_file"
        assert calls[1].data.tool_args["title"] == "Read README.md"

    def test_permission_request_becomes_a_request_approval_call(self) -> None:
        """The pause is rendered as an ordinary Lemma approval, so every client
        that already knows how to show one needs no Agent Host special case."""
        n = _normalizer()
        out = n.normalize(_permission(1, "perm-1", title="Run rm -rf build"))

        calls = [m for m in _messages(out) if m.data.tool_call_id is not None]
        assert [m.data.tool_name for m in calls] == ["request_approval"]
        assert calls[0].data.tool_call_id == "agent-host-permission:perm-1"
        assert calls[0].data.tool_args["title"] == "Run rm -rf build"
        assert calls[0].data.tool_args["tool_name"] == "exec_command"
        assert not any(e.type is AgentEventType.ERROR for e in out)

    def test_permission_request_does_not_end_the_run(self) -> None:
        """WAITING terminates a run. The host holds this request open *inside* a
        run that is still going, so emitting WAITING would strand everything the
        agent does after the decision."""
        n = _normalizer()
        out = n.normalize(_permission(1, "perm-1"))

        assert not any(is_terminal_event(e) for e in out)
        assert not any(e.type is AgentEventType.WAITING for e in out)

    def test_permission_status_carries_what_a_surface_needs_to_render(self) -> None:
        """The STATUS event is the only pause signal left, so it must carry the
        approval's identity or Slack/Teams/Telegram render nothing."""
        n = _normalizer()
        out = n.normalize(_permission(1, "perm-1"))

        statuses = [e.data for e in out if e.type is AgentEventType.STATUS]
        assert [s["status"] for s in statuses] == ["permission_request"]
        assert statuses[0]["kind"] == "request_approval"
        assert statuses[0]["tool_call_id"] == "agent-host-permission:perm-1"

    def test_an_unanswered_permission_is_closed_at_terminal(self) -> None:
        """A card whose run is gone must stop offering buttons.

        The host is no longer holding the request — its own timeout denied it
        half an hour in — so the only thing left to press was a button that
        lands on a dead run. One user pressed "Always allow" on a card four
        hours after the run behind it had failed, and nothing happened.
        """
        n = _normalizer()
        n.normalize(_permission(1, "perm-1"))

        out = n.normalize(_event(2, AgentHostEventType.TERMINAL, {"state": "FAILED"}))

        returns = [
            message
            for message in _messages(out)
            if message.data.tool_call_id == "agent-host-permission:perm-1"
        ]
        assert [message.data.tool_name for message in returns] == ["request_approval"]
        assert returns[0].data.tool_result["success"] is False
        # What every renderer already keys on to draw an unanswered interaction
        # as spent rather than live.
        assert returns[0].data.tool_result["interaction_fallback"] is True


class TestStructuredFinalAnswer:
    """How a structured result gets back out of an Agent Host run.

    The final answer is recognised by the marker the tool stamps into its own
    result rather than by the call's name, wherever the host's output left it:
    the unwrapped value, a text block it could not unwrap, or the arguments.
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

    def _close_tool_call(
        self, output: object = None, *, tool_input: object = None
    ) -> list:
        return [
            _call(1, "call-1", "final_answer", tool_input or {}, source="lemma"),
            _result(2, "call-1", output),
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
        out = _run(n, self._close_tool_call(self.RECORD))

        metadata = self._final_metadata(out)
        assert metadata["structured_output"] == {"label": "spam"}
        assert metadata["final_answer_status"] == "COMPLETED"
        assert metadata["tool_call_id"] == "call-1"

    def test_arguments_are_read_when_the_adapter_reports_no_output(self) -> None:
        """An adapter that reports no output still sent the arguments, and for
        this tool the arguments are the answer."""
        n = self._structured()
        out = _run(n, self._close_tool_call(None, tool_input=self.RECORD))

        assert self._final_metadata(out)["structured_output"] == {"label": "spam"}

    def test_text_only_result_is_recognised(self) -> None:
        """An envelope the host could not unwrap still carries the marker in its
        text block."""
        n = self._structured()
        out = _run(
            n,
            self._close_tool_call(
                [
                    {"type": "text", "text": json.dumps(self.RECORD)},
                    {"type": "text", "text": "Answer recorded."},
                ]
            ),
        )

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
                {**self.RECORD, "output": {"label": "x", "deep": deep}}
            ),
        )

        assert self._final_metadata(out)["structured_output"]["deep"] == deep

    def test_the_last_call_wins(self) -> None:
        n = self._structured()
        events = [
            *self._close_tool_call(self.RECORD),
            _call(3, "call-2", "final_answer", source="lemma"),
            _result(4, "call-2", {**self.RECORD, "output": {"label": "ham"}}),
        ]
        out = _run(n, events)

        assert self._final_metadata(out)["structured_output"] == {"label": "ham"}

    def test_a_permission_pause_does_not_burn_the_answer(self) -> None:
        """A mid-run pause flushes with final=False; the record must survive to
        the terminal flush or a run that pauses loses its result."""
        n = self._structured()
        events = [
            *self._close_tool_call(self.RECORD),
            _permission(3, "perm-1"),
        ]
        out = _run(n, events)

        assert self._final_metadata(out)["structured_output"] == {"label": "spam"}

    def test_the_written_answer_survives_a_run_that_talked_on_the_way(self) -> None:
        """The report the agent wrote, not the last thing it muttered.

        This was `message or answer`: any accumulated text at all won, and on a
        long run the accumulation was never empty -- so the answer the agent
        produced by calling the tool was dropped from the message body and
        survived only in metadata. The user saw a paragraph of narration where
        the report should have been.
        """
        n = self._structured()
        n.normalize(
            _event(
                1,
                AgentHostEventType.AGENT_MESSAGE_UPSERT,
                {"text": "Checking the last thing before I write this up."},
            )
        )
        n.adopt_final_answer({**self.RECORD, "output": {"label": "the actual report"}})
        out = n.normalize(
            _event(9, AgentHostEventType.TERMINAL, {"state": "SUCCEEDED"})
        )

        answer = [
            message.data.text
            for message in _text_messages(out)
            if message.data.metadata.get("is_final_answer")
        ]
        assert answer, "the run produced no final answer at all"
        assert "the actual report" in answer[0]
        assert "muttered" not in answer[0]
        assert "Checking the last thing" not in answer[0], (
            "the narration replaced the answer instead of preceding it"
        )

    def test_a_recorded_answer_overrides_what_the_stream_inferred(self) -> None:
        """The tool's own record is the authority; the stream is a heuristic."""
        n = self._structured()
        for event in self._close_tool_call(self.RECORD):
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


class TestAFailureIsNotAlsoAnAnswer:
    """A signed-out agent reported itself twice, and cost Retry doing it.

    An adapter that cannot start reports its own error as an ordinary
    `agent_message_chunk` — which becomes assistant text in the transcript —
    and the Agent Host then rewrites the same failure into words the user can
    act on and sends it again as the terminal message. Both landed, so the
    screenshot shows a bare "Failed to authenticate: OAuth session expired…"
    above a card explaining that Claude Code needs signing in.

    Worse than untidy: Lemma only offers Retry on a failed run whose messages
    are all the user's (`AgentRun.is_safely_retryable`), because retrying a run
    that produced output can duplicate work. So the stray assistant message was
    also what removed the button from the one failure a retry obviously fixes.

    The host says which of its terminal messages are rewrites; nothing here
    guesses.
    """

    def test_a_superseded_stream_leaves_no_assistant_message(self) -> None:
        n = _normalizer()
        n.normalize(
            _event(
                1,
                AgentHostEventType.AGENT_MESSAGE_CHUNK,
                {"text": "Failed to authenticate: OAuth session expired"},
            )
        )
        out = n.normalize(
            _event(
                2,
                AgentHostEventType.TERMINAL,
                {
                    "state": "FAILED",
                    "message": "Claude Code is installed on this computer but not signed in.",
                    "supersedes_stream": True,
                },
            )
        )

        assert _text_messages(out) == []
        assert [e.type for e in out if is_terminal_event(e)] == [AgentEventType.ERROR]

    def test_the_failure_itself_still_reaches_the_user(self) -> None:
        """Dropping the duplicate must not drop the explanation with it."""
        n = _normalizer()
        n.normalize(
            _event(
                1, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "raw adapter error"}
            )
        )
        out = n.normalize(
            _event(
                2,
                AgentHostEventType.TERMINAL,
                {
                    "state": "FAILED",
                    "message": "Claude Code is installed on this computer but not signed in.",
                    "supersedes_stream": True,
                },
            )
        )

        terminal = next(e for e in out if is_terminal_event(e))
        assert "not signed in" in str(terminal.data)

    def test_a_partial_answer_is_never_thrown_away(self) -> None:
        """The line this must not cross.

        A run that answered for three paragraphs and then hit its deadline
        keeps all three — and keeps blocking Retry, which is correct: that work
        happened and repeating it could repeat its side effects. Only a message
        the host says it is restating is dropped.
        """
        n = _normalizer()
        n.normalize(
            _event(
                1,
                AgentHostEventType.AGENT_MESSAGE_CHUNK,
                {"text": "a real partial answer"},
            )
        )
        out = n.normalize(
            _event(
                2,
                AgentHostEventType.TERMINAL,
                {"state": "FAILED", "message": "Agent Host run deadline elapsed"},
            )
        )

        assert [m.data.text for m in _text_messages(out)] == ["a real partial answer"]

    def test_reasoning_survives_a_superseded_failure(self) -> None:
        """A thought is never mistaken for an answer, and it is the only record
        of what the agent was doing when it failed."""
        n = _normalizer()
        n.normalize(
            _event(
                1,
                AgentHostEventType.AGENT_THOUGHT_CHUNK,
                {"text": "checking credentials"},
            )
        )
        n.normalize(
            _event(
                2,
                AgentHostEventType.AGENT_MESSAGE_CHUNK,
                {"text": "Failed to authenticate"},
            )
        )
        out = n.normalize(
            _event(
                3,
                AgentHostEventType.TERMINAL,
                {
                    "state": "FAILED",
                    "message": "Claude Code is installed on this computer but not signed in.",
                    "supersedes_stream": True,
                },
            )
        )

        assert [m.data.text for m in _text_messages(out)] == ["checking credentials"]
