"""A host agent's tool calls, as conversation messages.

The host names every call and shapes its input and output
(docs/architecture/agent-host-events.md); this side maps each ``tool_call`` and
``tool_call_result`` to one message, one for one. The guessing these tests used
to pin -- reading a name out of ``_meta`` or ``kind``, joining an argv, digging
stdout out of ``formatted_output`` -- is the host's job now and is tested
against recorded transcripts in ``desktop/agent-host/tests/normalize_golden.rs``.
What is pinned here is what only this side decides: when a call is dropped,
what a failure looks like, what streams live and what is persisted.
"""

from __future__ import annotations

import json

from uuid import uuid7

import pytest

from app.modules.agent.domain.agent_host import AgentHostEventType
from app.modules.agent.domain.value_objects import AgentEventType, MessageKind
from app.modules.agent.infrastructure.harnesses.agent_host.events import (
    TOOL_OUTPUT_TOKEN_KIND,
    AgentHostEventEnvelope,
    AgentHostEventNormalizer,
)
from app.modules.agent.infrastructure.harnesses.agent_host.tool_payload import (
    _MAX_TOOL_STRING_CHARACTERS,
)

pytestmark = pytest.mark.unit


def _normalizer() -> AgentHostEventNormalizer:
    return AgentHostEventNormalizer(
        agent_run_id=uuid7(), model_name="test-model", harness_key="codex"
    )


def _event(
    sequence: int, event_type: AgentHostEventType, payload: dict, object_id=None
) -> AgentHostEventEnvelope:
    return AgentHostEventEnvelope(
        sequence=sequence, type=event_type.value, object_id=object_id, payload=payload
    )


def _call(
    sequence: int,
    call_id: str,
    name: str,
    tool_input: object = None,
    *,
    source: str = "native",
    **tool: object,
) -> AgentHostEventEnvelope:
    parent_call_id = tool.pop("parent_call_id", None)
    return _event(
        sequence,
        AgentHostEventType.TOOL_CALL,
        {
            "tool": {"name": name, "source": source, **tool},
            "input": tool_input if tool_input is not None else {},
            "parent_call_id": parent_call_id,
        },
        call_id,
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
        call_id,
    )


def _messages(events):
    return [e.data for e in events if e.type is AgentEventType.MESSAGE]


def _feed(normalizer, *rows) -> list:
    return [event for row in rows for event in normalizer.normalize(row)]


class TestCanonicalCalls:
    def test_a_call_and_its_return_carry_the_hosts_name_and_values(self) -> None:
        n = _normalizer()
        out = _feed(
            n,
            _call(
                1,
                "exec-1",
                "exec_command",
                {"cmd": "echo hi", "workdir": "/w"},
                title="echo hi",
                kind="execute",
            ),
            _result(2, "exec-1", {"exit_code": 0, "stdout": "hi\n"}),
        )

        call, result = _messages(out)
        assert call.kind is MessageKind.TOOL_CALL
        assert (call.tool_name, call.tool_call_id) == ("exec_command", "exec-1")
        assert call.tool_args == {"cmd": "echo hi", "workdir": "/w"}
        assert call.metadata["tool_title"] == "echo hi"
        assert call.metadata["tool_kind"] == "execute"
        assert call.metadata["tool_source"] == "native"
        assert call.metadata["agent_host_object_id"] == "exec-1"
        assert result.kind is MessageKind.TOOL_RETURN
        assert (result.tool_name, result.tool_call_id) == ("exec_command", "exec-1")
        assert result.tool_result == {"exit_code": 0, "stdout": "hi\n"}

    def test_a_call_is_announced_once_even_if_reported_twice(self) -> None:
        n = _normalizer()
        out = _feed(
            n,
            _call(1, "c1", "read_file", {"file_path": "a"}),
            _call(2, "c1", "read_file", {"file_path": "a"}),
            _result(3, "c1"),
            _result(4, "c1"),
        )

        kinds = [m.kind for m in _messages(out)]
        assert kinds == [MessageKind.TOOL_CALL, MessageKind.TOOL_RETURN]

    def test_a_third_party_mcp_call_says_which_server(self) -> None:
        n = _normalizer()
        (call,) = _messages(
            n.normalize(_call(1, "c1", "create_issue", source="mcp", server="github"))
        )
        assert call.tool_name == "create_issue"
        assert call.metadata["tool_source"] == "mcp"
        assert call.metadata["tool_server"] == "github"

    def test_a_subagent_call_names_its_parent(self) -> None:
        n = _normalizer()
        (call,) = _messages(
            n.normalize(_call(1, "c2", "read_file", parent_call_id="task-1"))
        )
        assert call.metadata["parent_call_id"] == "task-1"

    def test_widget_arguments_are_not_truncated(self) -> None:
        """Bounding a result guards against a megabyte of stdout. Bounding the
        arguments is data loss: a WIDGET carries its whole document in
        ``content``, and the 4096-character ceiling replaced it with a
        placeholder, leaving the view nothing to render."""
        document = "<div>" + ("x" * 20_000) + "</div>"
        n = _normalizer()
        (call,) = _messages(
            n.normalize(
                _call(
                    1,
                    "c1",
                    "display_resource",
                    {"type": "WIDGET", "content": document},
                    source="lemma",
                )
            )
        )
        assert call.tool_args["content"] == document

    def test_a_long_output_is_bounded_but_keeps_its_head(self) -> None:
        n = _normalizer()
        output = "HEAD " + "x" * (_MAX_TOOL_STRING_CHARACTERS * 2)
        out = _feed(n, _call(1, "c1", "read_file"), _result(2, "c1", output))

        stored = _messages(out)[1].tool_result
        assert stored["truncated"] is True
        assert stored["preview"].startswith("HEAD ")

    def test_an_unpaired_result_puts_nothing_on_the_record(self) -> None:
        """The host opens any call it closes. A result that pairs with nothing
        is logged, never written: that is what left orphaned returns in
        conversations."""
        n = _normalizer()
        assert _messages(n.normalize(_result(1, "ghost"))) == []

    def test_a_malformed_call_does_not_end_the_run(self) -> None:
        n = _normalizer()
        out = n.normalize(
            _event(1, AgentHostEventType.TOOL_CALL, {"tool": {"name": ""}}, "c1")
        )
        assert _messages(out) == []
        assert not any(e.type is AgentEventType.ERROR for e in out)


class TestFailures:
    def test_a_failed_call_keeps_its_output_and_says_why(self) -> None:
        """The card used to show only "failed": the output was thrown away."""
        n = _normalizer()
        out = _feed(
            n,
            _call(1, "c1", "exec_command", {"cmd": "false"}),
            _result(
                2,
                "c1",
                {"exit_code": 1, "stderr": "boom"},
                status="failed",
                error="exited with code 1",
            ),
        )
        assert _messages(out)[1].tool_result == {
            "exit_code": 1,
            "stderr": "boom",
            "success": False,
            "error": "exited with code 1",
        }

    def test_a_non_object_output_is_wrapped(self) -> None:
        n = _normalizer()
        out = _feed(
            n,
            _call(1, "c1", "grep"),
            _result(2, "c1", "partial", status="cancelled", error="cancelled"),
        )
        assert _messages(out)[1].tool_result == {
            "output": "partial",
            "success": False,
            "error": "cancelled",
        }

    def test_a_denied_call_with_nothing_to_show_says_so(self) -> None:
        n = _normalizer()
        out = _feed(
            n, _call(1, "c1", "web_fetch"), _result(2, "c1", None, status="denied")
        )
        assert _messages(out)[1].tool_result == {
            "success": False,
            "error": "not allowed",
        }


class TestPausingTools:
    """Lemma's own pausing tools are recorded by Lemma, not by the harness.

    Lemma writes these itself when the MCP call arrives, because only an id
    Lemma minted can be answered: the approval endpoint, the wait timer and the
    resume all address a call by its id, and the one the harness reports here
    belongs to a namespace none of them can reach. Emitting both put two
    identical questions in the conversation, one of them on a card whose
    buttons resolved nothing.
    """

    def test_lemmas_ask_user_is_left_to_lemma_to_record(self) -> None:
        n = _normalizer()
        out = _feed(
            n,
            _call(1, "host-1", "ask_user", {"question": "Which?"}, source="lemma"),
            _event(2, AgentHostEventType.TOOL_CALL_PROGRESS, {"text": "…"}, "host-1"),
            _result(3, "host-1", {"answer": "the blue one"}),
            _event(4, AgentHostEventType.TERMINAL, {"state": "SUCCEEDED"}),
        )
        assert [m for m in _messages(out) if m.tool_call_id == "host-1"] == []
        assert not any(
            e.type is AgentEventType.TOKEN and e.data.get("tool_call_id") == "host-1"
            for e in out
        )

    def test_a_native_tool_with_the_same_name_is_still_recorded(self) -> None:
        """The drop is for *Lemma's* tool. An agent's own tool that happens to
        share the name is recorded nowhere else, so dropping it loses it."""
        n = _normalizer()
        out = _feed(
            n,
            _call(1, "c1", "ask_user", {"question": "Which?"}, source="native"),
            _result(2, "c1", {"answer": "blue"}),
        )
        assert [m.kind for m in _messages(out)] == [
            MessageKind.TOOL_CALL,
            MessageKind.TOOL_RETURN,
        ]

    def test_an_ordinary_lemma_tool_is_still_recorded(self) -> None:
        n = _normalizer()
        out = n.normalize(_call(1, "c1", "exec_command", source="lemma"))
        assert len(_messages(out)) == 1


class TestLiveTokens:
    def test_a_call_shows_as_running_after_its_card(self) -> None:
        """The token comes after the message: a client clears its live tool
        when a message lands, so the indicator lasts until the return."""
        n = _normalizer()
        out = n.normalize(_call(1, "c1", "read_file", {"file_path": "a.md"}))

        assert [e.type for e in out] == [AgentEventType.MESSAGE, AgentEventType.TOKEN]
        token = out[1].data
        assert token["kind"] == "tool"
        assert json.loads(token["data"]) == {
            "tool_name": "read_file",
            "tool_call_id": "c1",
            "args": {"file_path": "a.md"},
        }

    def test_progress_streams_but_is_never_persisted(self) -> None:
        n = _normalizer()
        n.normalize(_call(1, "c1", "exec_command", {"cmd": "make"}))
        out = n.normalize(
            _event(
                2, AgentHostEventType.TOOL_CALL_PROGRESS, {"text": "building\n"}, "c1"
            )
        )

        assert _messages(out) == []
        assert [e.data for e in out] == [
            {"kind": TOOL_OUTPUT_TOKEN_KIND, "data": "building\n", "tool_call_id": "c1"}
        ]


class TestPlans:
    def test_a_plan_is_an_ordinary_update_plan_pair(self) -> None:
        """Every adapter's to-do list reaches this side as ``update_plan``, the
        tool the plan card already renders, so nothing here is plan-specific."""
        todos = [{"content": "Read the spec", "status": "completed"}]
        n = _normalizer()
        out = _feed(
            n,
            _call(1, "plan-1", "update_plan", {"todos": todos}),
            _result(2, "plan-1", {"todos": todos}),
        )

        call, result = _messages(out)
        assert (call.tool_name, call.tool_args) == ("update_plan", {"todos": todos})
        assert (result.tool_name, result.tool_result) == (
            "update_plan",
            {"todos": todos},
        )


class TestRunEnd:
    def test_an_unfinished_call_is_closed_at_terminal(self) -> None:
        n = _normalizer()
        n.normalize(_call(1, "c1", "read_file"))
        out = n.normalize(_event(2, AgentHostEventType.TERMINAL, {"state": "FAILED"}))

        (closed,) = [m for m in _messages(out) if m.tool_call_id == "c1"]
        assert closed.kind is MessageKind.TOOL_RETURN
        assert closed.tool_name == "read_file"
        assert closed.tool_result["success"] is False
        assert out[-1].type is AgentEventType.ERROR

    def test_a_closed_call_is_not_closed_again(self) -> None:
        n = _normalizer()
        _feed(n, _call(1, "c1", "read_file"), _result(2, "c1"))
        out = n.normalize(
            _event(3, AgentHostEventType.TERMINAL, {"state": "SUCCEEDED"})
        )

        assert [m for m in _messages(out) if m.tool_call_id == "c1"] == []


class TestUsage:
    def test_one_usage_event_is_one_request_with_its_tokens(self) -> None:
        n = _normalizer()
        out = n.normalize(
            _event(
                1,
                AgentHostEventType.USAGE,
                {
                    "input_tokens": 1217,
                    "output_tokens": 5,
                    "cached_input_tokens": 20224,
                    "reasoning_tokens": 0,
                    "total_tokens": 21446,
                },
            )
        )

        (usage,) = [e.data for e in out if e.type is AgentEventType.USAGE]
        assert (usage.input_tokens, usage.output_tokens) == (1217, 5)
        assert usage.request_count == 1
        assert usage.model_name == "test-model"
        assert usage.metadata["cache_read_tokens"] == 20224
        assert usage.metadata["reasoning_tokens"] == 0
        assert usage.metadata["total_tokens"] == 21446

    def test_a_context_window_report_is_not_usage(self) -> None:
        """ACP's ``usage_update`` is the context window, now carried by
        ``session_update``. Read as usage, each one billed a request with zero
        tokens."""
        n = _normalizer()
        out = n.normalize(
            _event(
                1,
                AgentHostEventType.SESSION_UPDATE,
                {"context": {"used": 20000, "size": 200000}},
            )
        )

        assert not any(e.type is AgentEventType.USAGE for e in out)
        assert [e.data["status"] for e in out] == ["session_update"]
