"""Normalizer behaviour on a single ordered event lane.

Several of these pin defects found reviewing the two-lane design: a stream key
leaking into message metadata, every flush claiming to be the final answer, and
an upsert re-emitting text the user had already seen.
"""

from __future__ import annotations

import json

from uuid import uuid7

from app.modules.agent.domain.agent_host import AgentHostEventType, AgentHostRunState
from app.modules.agent.domain.value_objects import AgentEventType, MessageKind
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
                _event(
                    3,
                    AgentHostEventType.TOOL_CALL_UPSERT,
                    {"name": "read_file"},
                    object_id="c1",
                ),
                _event(
                    4,
                    AgentHostEventType.TOOL_CALL_UPDATE,
                    {"status": "COMPLETED", "result": "ok"},
                    object_id="c1",
                ),
                _event(5, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "! there"}),
                _event(
                    6, AgentHostEventType.AGENT_MESSAGE_UPSERT, {"text": "! there"}
                ),
            ],
        )
        assert _tokens(out) == "Hello! there"
        assert _final_text(out) == "Hello! there", (
            "what is persisted must match what the user watched stream"
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


class TestToolNames:
    """What a tool call is *called*, which is what every card and icon keys on.

    The payloads here are the ones a real Claude Code run emits over ACP, taken
    from a desktop install's event journal: no `name` field anywhere, the real
    name under `_meta.claudeCode.toolName`, and a `kind` that is a category
    rather than a name.
    """

    def _call(self, payload: dict) -> str:
        n = _normalizer()
        out = n.normalize(
            _event(1, AgentHostEventType.TOOL_CALL_UPSERT, payload, object_id="c1")
        )
        return _messages(out)[0].data.tool_name

    def test_a_lemma_mcp_tool_is_the_tool_the_pod_agent_calls(self) -> None:
        """The whole point: one tool, one name. A local agent namespaces Lemma's
        run-scoped MCP server, so the same tool the pod agent calls as
        `pod_write_file` arrived as `mcp__lemma__lemma_pod_write_file` and
        rendered as its own unrecognised tool, with no icon and no card."""
        assert (
            self._call(
                {
                    "_meta": {
                        "claudeCode": {"toolName": "mcp__lemma__lemma_pod_write_file"}
                    },
                    "title": "mcp__lemma__lemma_pod_write_file",
                    "toolCallId": "c1",
                }
            )
            == "pod_write_file"
        )

    def test_every_namespacing_shape_lands_on_the_same_tool(self) -> None:
        for reported in (
            "mcp__lemma__lemma_exec_command",
            "mcp__lemma_tools__lemma_exec_command",
            "lemma__lemma_exec_command",
            "lemma_exec_command",
            "exec_command",
        ):
            assert self._call({"title": reported}) == "exec_command", reported

    def test_a_third_party_mcp_tool_keeps_its_own_name(self) -> None:
        assert (
            self._call({"title": "mcp__github__create_issue"})
            == "mcp__github__create_issue"
        )

    def test_the_real_name_beats_the_acp_category(self) -> None:
        """`kind` is `fetch` for a web search, a page fetch and anything else an
        adapter files under it, so reading it as the name showed every one of
        them as "Fetch" — and made the approval card name a category too."""
        assert (
            self._call(
                {
                    "_meta": {"claudeCode": {"toolName": "WebSearch"}},
                    "kind": "fetch",
                    "title": "Web search",
                }
            )
            == "WebSearch"
        )

    def test_a_category_is_still_better_than_a_human_title(self) -> None:
        """Without `_meta` the title is whatever the adapter wrote for a human —
        for Bash that is the command itself — so `kind` still comes first."""
        assert self._call({"kind": "execute", "title": "npm test"}) == "exec_command"

    def test_other_is_not_a_name(self) -> None:
        """`other` is the kind adapters give everything they have no category
        for, MCP tools included. Recorded as the name it collapsed them all."""
        assert self._call({"kind": "other", "title": "lemma_read_table"}) == "read_table"

    def test_the_approval_card_names_the_tool_it_interrupts(self) -> None:
        n = _normalizer()
        n.normalize(
            _event(
                1,
                AgentHostEventType.TOOL_CALL_UPSERT,
                {"_meta": {"claudeCode": {"toolName": "WebSearch"}}, "kind": "fetch"},
                object_id="toolu_1",
            )
        )
        out = n.normalize(
            _event(
                2,
                AgentHostEventType.PERMISSION_REQUEST,
                {
                    "toolCall": {"toolCallId": "toolu_1", "kind": "fetch", "title": "x"},
                    "options": [{"optionId": "allow", "kind": "allow_once"}],
                },
                object_id="toolu_1",
            )
        )

        calls = [m for m in _messages(out) if m.data.tool_call_id is not None]
        assert calls[0].data.tool_args["tool_name"] == "WebSearch"


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

    def test_a_pausing_tool_is_left_to_lemma_to_record(self) -> None:
        """The harness's copy of `ask_user` never reaches the conversation.

        Lemma records these itself, when the MCP call arrives, because only an
        id Lemma minted can be answered: the approval endpoint, the snooze
        timer and the resume all address a call by its id, and the one the
        harness reports here belongs to a namespace none of them can reach.
        Emitting both put two identical questions in the conversation, one of
        them on a card whose buttons resolved nothing.
        """
        n = _normalizer()
        opened = n.normalize(
            _event(
                1,
                AgentHostEventType.TOOL_CALL_UPSERT,
                {"name": "ask_user", "rawInput": {"question": "Which one?"}},
                object_id="host-call-1",
            )
        )
        closed = n.normalize(
            _event(
                2,
                AgentHostEventType.TOOL_CALL_UPDATE,
                {"status": "COMPLETED", "result": {"answer": "the blue one"}},
                object_id="host-call-1",
            )
        )
        assert _messages(opened) == []
        assert _messages(closed) == []

    def test_an_ordinary_tool_is_still_recorded_from_the_harness(self) -> None:
        """The suppression is by tool, not a general silencing of the lane."""
        n = _normalizer()
        opened = n.normalize(
            _event(
                1,
                AgentHostEventType.TOOL_CALL_UPSERT,
                {"name": "exec_command", "rawInput": {"command": "ls"}},
                object_id="host-call-2",
            )
        )
        assert len(_messages(opened)) == 1

    def test_a_streamed_call_keeps_the_arguments_that_arrive_after_it(self) -> None:
        """The sequence a streaming adapter really sends, in order.

        Claude Code surfaces the call at ``content_block_start``, before the
        model has written its input, so the first ``tool_call`` carries
        ``rawInput: {}``. The real arguments follow on a ``tool_call_update``
        with no status at all — which the normalizer used to drop, because only
        terminal statuses were treated as news. Every streamed tool call
        therefore rendered with empty arguments for the life of the
        conversation, and anything built from them had nothing to build from.
        """
        n = _normalizer()
        request = {"type": "WIDGET", "content": "<div>hello</div>"}

        opened = n.normalize(
            _event(
                1,
                AgentHostEventType.TOOL_CALL_UPSERT,
                {"rawInput": {}, "status": "pending", "title": "display_resource"},
                object_id="call-1",
            )
        )
        refined = n.normalize(
            _event(
                2,
                AgentHostEventType.TOOL_CALL_UPDATE,
                {"rawInput": {"request": request}, "title": "display_resource"},
                object_id="call-1",
            )
        )
        # The update that carries no arguments is the input's full stop.
        settled = n.normalize(
            _event(3, AgentHostEventType.TOOL_CALL_UPDATE, {}, object_id="call-1")
        )

        # Nothing durable while the arguments are still being written; a message
        # is appended and never revised, so announcing `{}` would pin `{}`.
        assert _messages(opened) == []
        assert _messages(refined) == []
        calls = _messages(settled)
        assert len(calls) == 1
        assert calls[0].data.tool_args == {"request": request}

    def test_a_call_is_announced_only_once_its_input_stops_growing(self) -> None:
        """An adapter streams a call's input as a growing prefix of its fields.

        Observed on the wire for a real `write_file`: `{path}` first, then
        `{path, content}` with 1126 more characters. Announcing on the first
        non-empty piece published a call missing most of its input, and a
        conversation message is appended rather than revised, so that was
        final. The update carrying no arguments at all is what says the input
        is done — and it still arrives before the tool runs.
        """
        n = _normalizer()
        document = "# Report\n" + ("detail " * 200)

        def feed(sequence: int, payload: dict) -> list:
            return _messages(
                n.normalize(
                    _event(
                        sequence,
                        AgentHostEventType.TOOL_CALL_UPDATE,
                        payload,
                        object_id="call-1",
                    )
                )
            )

        n.normalize(
            _event(
                1,
                AgentHostEventType.TOOL_CALL_UPSERT,
                {"rawInput": {}},
                object_id="call-1",
            )
        )
        assert feed(2, {"rawInput": {"path": "report.md"}}) == []
        assert feed(3, {"rawInput": {"path": "report.md", "content": document}}) == []
        # The input has stopped arriving; now the call is worth writing down.
        announced = feed(4, {})

        assert len(announced) == 1
        assert announced[0].data.tool_args == {
            "path": "report.md",
            "content": document,
        }

    def test_a_later_empty_update_does_not_erase_the_arguments(self) -> None:
        """An adapter sends several refinements, and most of them carry nothing.

        Observed on the wire: the update carrying ``rawInput`` is followed
        immediately by one holding only the call's id. Folding that in
        naively puts the empty value back and loses what was just learned.
        """
        n = _normalizer()
        n.normalize(
            _event(
                1,
                AgentHostEventType.TOOL_CALL_UPSERT,
                {"rawInput": {}},
                object_id="call-1",
            )
        )
        n.normalize(
            _event(
                2,
                AgentHostEventType.TOOL_CALL_UPDATE,
                {"rawInput": {"path": "README.md"}},
                object_id="call-1",
            )
        )
        # The argument-less update ends the input and releases the call.
        trailing = _messages(
            n.normalize(
                _event(3, AgentHostEventType.TOOL_CALL_UPDATE, {}, object_id="call-1")
            )
        )
        closed = n.normalize(
            _event(
                4,
                AgentHostEventType.TOOL_CALL_UPDATE,
                {"status": "COMPLETED", "rawOutput": "ok"},
                object_id="call-1",
            )
        )

        assert len(trailing) == 1
        assert trailing[0].data.tool_args == {"path": "README.md"}
        # And the close adds only the return, never a second call card.
        assert [m.data.kind for m in _messages(closed)] == [MessageKind.TOOL_RETURN]

    def test_a_call_released_at_its_close_reads_the_closing_update(self) -> None:
        """The closing update is often the first thing that names a tool.

        A call held for arguments that never came was announced from its
        opening alone — an anonymous ``tool`` with ``{}`` — while the name and
        the input sat in the very event that triggered the release. Both cards
        then disagreed with what actually ran.
        """
        n = _normalizer()
        n.normalize(
            _event(
                1,
                AgentHostEventType.TOOL_CALL_UPSERT,
                {"rawInput": {}},
                object_id="call-1",
            )
        )
        closed = n.normalize(
            _event(
                2,
                AgentHostEventType.TOOL_CALL_UPDATE,
                {
                    "status": "COMPLETED",
                    "_meta": {"claudeCode": {"toolName": "read_file"}},
                    "rawInput": {"path": "README.md"},
                    "rawOutput": "# Lemma",
                },
                object_id="call-1",
            )
        )

        call, result = (m.data for m in _messages(closed))
        assert call.kind is MessageKind.TOOL_CALL
        assert call.tool_name == "read_file"
        assert call.tool_args == {"path": "README.md"}
        # And the return agrees with it, rather than with the placeholder the
        # call opened under.
        assert result.tool_name == "read_file"

    def test_a_call_whose_arguments_never_arrive_is_still_announced(self) -> None:
        """Holding is for arguments in flight, never a way to lose a call.

        If the turn ends while a call is still held — cancelled, adapter died,
        or a tool genuinely invoked with nothing — the call happened and the
        conversation still owes it a card, ahead of the return that closes it.
        """
        n = _normalizer()
        n.normalize(
            _event(
                1,
                AgentHostEventType.TOOL_CALL_UPSERT,
                {"rawInput": {}, "title": "read_file"},
                object_id="call-1",
            )
        )
        finished = n.normalize(
            _event(2, AgentHostEventType.TERMINAL, {"state": "FAILED"})
        )

        kinds = [m.data.kind for m in _messages(finished)]
        assert MessageKind.TOOL_CALL in kinds
        assert kinds.index(MessageKind.TOOL_CALL) < kinds.index(MessageKind.TOOL_RETURN)

    def test_widget_arguments_are_not_truncated(self) -> None:
        """Bounding a result guards against a megabyte of stdout. Bounding the
        arguments is data loss: a WIDGET carries its whole document in
        ``content``, and the 4096-character ceiling replaced it with a
        placeholder, leaving the view nothing to render."""
        n = _normalizer()
        document = "<div>" + ("x" * 20_000) + "</div>"

        n.normalize(
            _event(
                1,
                AgentHostEventType.TOOL_CALL_UPSERT,
                {"rawInput": {}},
                object_id="call-1",
            )
        )
        n.normalize(
            _event(
                2,
                AgentHostEventType.TOOL_CALL_UPDATE,
                {"rawInput": {"request": {"type": "WIDGET", "content": document}}},
                object_id="call-1",
            )
        )
        settled = n.normalize(
            _event(3, AgentHostEventType.TOOL_CALL_UPDATE, {}, object_id="call-1")
        )

        assert _messages(settled)[0].data.tool_args["request"]["content"] == document

    def test_an_mcp_result_is_the_value_the_tool_returned(self) -> None:
        """An adapter reports an MCP call's output as the MCP envelope, while
        the in-process harness stores what the tool returned. The frontend reads
        a result as an object, so the envelope arrived as ``{"output": [...]}``
        and the served view's ``url`` was a level too deep to find."""
        n = _normalizer()
        n.normalize(
            _event(1, AgentHostEventType.TOOL_CALL_UPSERT, {}, object_id="call-1")
        )
        closed = n.normalize(
            _event(
                2,
                AgentHostEventType.TOOL_CALL_UPDATE,
                {
                    "status": "COMPLETED",
                    "rawOutput": {
                        "content": [
                            {
                                "type": "text",
                                "text": '{"success": true, "url": "https://x/y"}',
                            }
                        ]
                    },
                },
                object_id="call-1",
            )
        )

        assert _messages(closed)[0].data.tool_result == {
            "success": True,
            "url": "https://x/y",
        }

    def test_a_multi_part_mcp_result_is_left_alone(self) -> None:
        """Only the unambiguous envelope is unwrapped. A genuinely multi-part
        result is not a wrapper around one value, and picking a part would lose
        the rest."""
        n = _normalizer()
        blocks = [
            {"type": "text", "text": '{"a": 1}'},
            {"type": "text", "text": '{"b": 2}'},
        ]
        n.normalize(
            _event(1, AgentHostEventType.TOOL_CALL_UPSERT, {}, object_id="call-1")
        )
        closed = n.normalize(
            _event(
                2,
                AgentHostEventType.TOOL_CALL_UPDATE,
                {"status": "COMPLETED", "rawOutput": {"content": blocks}},
                object_id="call-1",
            )
        )

        assert _messages(closed)[0].data.tool_result == {"content": blocks}

    def test_an_untagged_call_and_its_update_are_the_same_call(self) -> None:
        """ACP's ToolCall has no required id, so an adapter can report a call
        and its completion with nothing linking them. Falling back to the event
        sequence gave the two different ids by construction: the call was left
        open and swept as abandoned, while the result addressed a call nobody
        held — one tool use rendering as two broken halves."""
        n = _normalizer()

        opened = n.normalize(
            _event(1, AgentHostEventType.TOOL_CALL_UPSERT, {"name": "read_file"})
        )
        closed = n.normalize(
            _event(
                2,
                AgentHostEventType.TOOL_CALL_UPDATE,
                {"status": "COMPLETED", "result": "ok"},
            )
        )
        finished = n.normalize(
            _event(3, AgentHostEventType.TERMINAL, {"state": "SUCCEEDED"})
        )

        call = _messages(opened)[0].data
        result = _messages(closed)[0].data
        assert call.tool_call_id == result.tool_call_id
        # And nothing is left over to be swept as an abandoned call.
        assert [
            m for m in _messages(finished) if m.data.tool_call_id == call.tool_call_id
        ] == []

    def test_two_untagged_calls_do_not_collapse_into_one(self) -> None:
        n = _normalizer()

        first = n.normalize(
            _event(1, AgentHostEventType.TOOL_CALL_UPSERT, {"name": "read_file"})
        )
        n.normalize(
            _event(2, AgentHostEventType.TOOL_CALL_UPDATE, {"status": "COMPLETED"})
        )
        second = n.normalize(
            _event(3, AgentHostEventType.TOOL_CALL_UPSERT, {"name": "write_file"})
        )

        assert (
            _messages(first)[0].data.tool_call_id
            != _messages(second)[0].data.tool_call_id
        )

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

    def test_an_unanswered_permission_is_closed_at_terminal(self) -> None:
        """A card whose run is gone must stop offering buttons.

        The host is no longer holding the request — its own timeout denied it
        half an hour in — so the only thing left to press was a button that
        lands on a dead run. One user pressed "Always allow" on a card four
        hours after the run behind it had failed, and nothing happened.
        """
        n = _normalizer()
        n.normalize(
            _event(
                1,
                AgentHostEventType.PERMISSION_REQUEST,
                {
                    "toolCall": {"toolCallId": "perm-1", "title": "Web search"},
                    "options": [{"optionId": "allow", "kind": "allow_once"}],
                },
                object_id="perm-1",
            )
        )

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
        assert [e.type for e in out if is_terminal_event(e)] == [
            AgentEventType.ERROR
        ]

    def test_the_failure_itself_still_reaches_the_user(self) -> None:
        """Dropping the duplicate must not drop the explanation with it."""
        n = _normalizer()
        n.normalize(
            _event(1, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "raw adapter error"})
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
            _event(1, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "a real partial answer"})
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
            _event(1, AgentHostEventType.AGENT_THOUGHT_CHUNK, {"text": "checking credentials"})
        )
        n.normalize(
            _event(2, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "Failed to authenticate"})
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
